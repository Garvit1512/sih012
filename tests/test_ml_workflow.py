"""Synthetic protocol/CPU training gates; these scores are not product accuracy."""
import json

import numpy as np
import pytest
import rasterio
from shapely.geometry import box

from backend.ml.evaluate import evaluate
from backend.ml.metrics import boundary_scores, class_scores, confusion, instance_scores
from backend.ml.protocol import read_manifest
from backend.ml.register import register
from backend.ml.roads import centerlines
from backend.ml.roads import centerline_scores
from backend.ml.train import train
from backend.ml.elevation import prepare_height
from backend.ml.functional_use import fit as fit_use, predict as predict_use
from backend.services.ingestion_service import sha256, ingest_site
from backend.services.model_service import models, require_model
from backend.services.prediction_service import inference, validate_features
from test_site_workflow import configured, fixture_sites, raster


@pytest.fixture
def training_manifest(tmp_path):
    samples=[]
    for i,(split,offset) in enumerate((("train",0),("train",100),("development",1000),("evaluation",2000))):
        image=raster(tmp_path/f"image-{i}.tif",offset=offset)
        labels=tmp_path/f"labels-{i}.tif"
        mask=np.zeros((64,64),np.uint8); mask[20:35,20:35]=1; mask[:,45:50]=2
        with rasterio.open(image) as src:
            profile=src.profile; profile.update(count=1)
        with rasterio.open(labels,"w",**profile) as dst: dst.write(mask,1)
        samples.append({"id":str(i),"site_id":"synthetic","block_id":str(i),"split":split,"reviewed":True,
                        "labels_locked_before_predictions":True,"image":image.name,"labels":labels.name,
                        "image_sha256":sha256(image),"labels_sha256":sha256(labels)})
    manifest={"schema_version":1,"task":"cover","classes":["background","building","road"],"analysis_crs":"EPSG:32643",
              "licence":"synthetic software fixture","spatial_buffer_m":50,"samples":samples}
    path=tmp_path/"manifest.json"; path.write_text(json.dumps(manifest)); return path


def changed(path, mutate):
    data=json.loads(path.read_text()); mutate(data); path.write_text(json.dumps(data))


def test_spatial_protocol_rejects_leakage_changed_labels_and_unlocked_evaluation(training_manifest):
    assert len(read_manifest(training_manifest)["samples"])==4
    original=training_manifest.read_text()
    changed(training_manifest,lambda m:m["samples"][2].update(block_id="0"))
    with pytest.raises(ValueError,match="spatial block"): read_manifest(training_manifest)
    training_manifest.write_text(original)
    changed(training_manifest,lambda m:m["samples"][3].update(labels_locked_before_predictions=False))
    with pytest.raises(ValueError,match="locked"): read_manifest(training_manifest)
    training_manifest.write_text(original)
    changed(training_manifest,lambda m:m["samples"][1].update(labels_sha256="changed"))
    with pytest.raises(ValueError,match="hash"): read_manifest(training_manifest)
    training_manifest.write_text(original)
    changed(training_manifest,lambda m:m["samples"][3].update(image=m["samples"][0]["image"],image_sha256=m["samples"][0]["image_sha256"]))
    with pytest.raises(ValueError,match="Identical imagery"): read_manifest(training_manifest)


def test_unverified_height_is_rejected(training_manifest):
    changed(training_manifest,lambda m:m["samples"][0].update(ndsm="height.tif"))
    with pytest.raises(ValueError,match="verified"): read_manifest(training_manifest)


def test_ndsm_requires_review_evidence_and_aligned_grids(configured,tmp_path):
    image=raster(tmp_path/"rgb.tif"); dsm=raster(tmp_path/"dsm.tif",count=1); dtm=raster(tmp_path/"dtm.tif",count=1)
    ingest_site(configured,image,"height","Synthetic height",elevation={"dsm":dsm,"dtm":dtm})
    site=configured.sites_dir/"height"
    with pytest.raises(ValueError,match="requires reviewed"): prepare_height(site,"test datum","reviewer","test source")
    result=prepare_height(site,"test datum","reviewer","synthetic fixture",True)
    assert result["valid_pixels"]==4096 and result["vertical_units"]=="metres" and result["range_m"]==[0,0]
    with rasterio.open(site/"ndsm.tif") as ds: assert ds.dtypes==("float32",) and ds.crs.to_epsg()==32643
    with pytest.raises(ValueError,match="overwrite"): prepare_height(site,"test datum","reviewer","synthetic fixture",True)


def test_known_metrics_and_road_diagnostics():
    matrix=confusion(np.array([[0,1],[1,255]]),np.array([[0,1],[0,0]]),2)
    scores=class_scores(matrix,["background","building"])
    assert scores["evaluated_pixels"]==3 and scores["classes"][1]["recall"]==.5
    instances=instance_scores([box(0,0,10,10),box(20,0,30,10)],[box(0,0,10,10),box(50,0,60,10)])
    assert (instances["tp"],instances["fp"],instances["fn"])==(1,1,1)
    boundary=boundary_scores(box(0,0,10,10),box(0,0,10,10),.3)
    assert boundary["precision"]==boundary["recall"]==1
    from shapely.geometry import GeometryCollection
    assert boundary_scores(box(0,0,10,10),GeometryCollection(),.3)["precision"] is None
    from rasterio.transform import from_origin
    road=np.zeros((32,32),bool); road[10:13,2:28]=True
    result=centerlines(road,from_origin(500000,2500000,.5,.5),"EPSG:32643")
    assert result["diagnostics"]["components"]==1 and result["features"]
    assert all(f["properties"]["access_status"]=="unverified" for f in result["features"])
    scores=centerline_scores(road,road,from_origin(500000,2500000,.5,.5),"EPSG:32643")
    assert scores["precision"] == scores["recall"] == 1


def test_functional_use_needs_evidence_and_can_remain_unknown():
    rows=[{"site_id":"synthetic","block_id":str(i),"split":"train","reviewed":True,"evidence_ids":[str(i)],
           "use_label":"class-a" if i<2 else "class-b","area_m2":10+i*100,"compactness":.6,
           "distance_to_road_m":2+i*5,"neighbours_within_50m":i} for i in range(4)]
    model=fit_use(rows)
    assert predict_use(model,{})["functional_use_suggestion"]=="unknown"
    nearby=predict_use(model,rows[0])
    assert nearby["functional_use_suggestion"]=="class-a" and nearby["functional_use"]=="unknown"
    far=predict_use(model,{**rows[0],"area_m2":10000000})
    assert far["functional_use_suggestion"]=="unknown"
    rows[0]["evidence_ids"]=[]
    with pytest.raises(ValueError,match="evidence"): fit_use(rows)


def test_cpu_training_freeze_evaluation_registration_and_inference(training_manifest,fixture_sites,tmp_path,monkeypatch):
    experiment=tmp_path/"experiment"
    report=train(training_manifest,experiment,epochs=1)
    assert report["status"]=="complete" and report["selected_epoch"]==1
    with pytest.raises(ValueError,match="overwrite"): train(training_manifest,experiment,epochs=1)
    evaluation=evaluate(experiment,tmp_path/"evaluation")
    assert evaluation["sample_count"]==1 and evaluation["overall"]["evaluated_pixels"]==4096
    with pytest.raises(ValueError,match="overwrite"): evaluate(experiment,tmp_path/"evaluation")
    model_root=tmp_path/"models"
    row=register(experiment,"test-cover","Synthetic cover candidate",model_root)
    assert row["status"]=="candidate"
    with pytest.raises(ValueError,match="exists"): register(experiment,"test-cover","Duplicate",model_root)
    model=require_model("test-cover",model_root)
    assert models(model_root)[-1]["available"]
    manifest=json.loads((fixture_sites.sites_dir/"first/manifest.json").read_text())
    output=tmp_path/"inference"; output.mkdir()
    fc,method=inference(manifest,fixture_sites.sites_dir/"first",{"target_res_m":.5,"min_area_m2":0,"threshold":.05},model,output,lambda *args:None)
    assert method=="land_cover_segmentation" and (output/"land_cover.tif").is_file()
    validate_features(manifest,fc)
    assert all(f["properties"]["functional_use"]=="unknown" for f in fc["features"])
    protocol_path=experiment/"protocol.json"
    frozen=protocol_path.read_text()
    changed(protocol_path,lambda p:p["parameters"]["evaluation_settings"].update(confidence_threshold=.01))
    with pytest.raises(ValueError,match="protocol changed"): evaluate(experiment,tmp_path/"changed-protocol")
    protocol_path.write_text(frozen)
    changed(training_manifest,lambda m:m.update(spatial_buffer_m=0))
    with pytest.raises(ValueError,match="changed"): evaluate(experiment,tmp_path/"changed-evaluation")
