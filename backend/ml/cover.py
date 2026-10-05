"""Supervised land-cover candidate; functional land use requires separate evidence."""
import json

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from shapely.geometry import mapping, shape
import torch

from ..utils.crs import to_wgs


def cover_model(classes, channels=3):
    import segmentation_models_pytorch as smp
    return smp.Unet(encoder_name="resnet18", encoder_weights=None, in_channels=channels, classes=classes)


@torch.no_grad()
def cover_probabilities(model, image, tile=256, overlap=64):
    """Hann-blended sliding tiles; probabilities remain uncalibrated model scores."""
    def starts(length):
        if length <= tile: return [0]
        return sorted(set([*range(0,length-tile,tile-overlap),length-tile]))
    h,w,c = image.shape
    ph,pw=max(h,tile),max(w,tile)
    image=np.pad(image,((0,ph-h),(0,pw-w),(0,0)),mode="reflect")
    weight=np.outer(np.hanning(tile+2)[1:-1],np.hanning(tile+2)[1:-1]).astype(np.float32)+1e-3
    acc=None; total=np.zeros((ph,pw),np.float32)
    for y in starts(ph):
        for x in starts(pw):
            chunk=torch.from_numpy(np.ascontiguousarray(image[y:y+tile,x:x+tile].transpose(2,0,1))).float()[None]
            p=torch.softmax(model(chunk),dim=1)[0].cpu().numpy()
            if acc is None: acc=np.zeros((p.shape[0],ph,pw),np.float32)
            acc[:,y:y+tile,x:x+tile]+=p*weight; total[y:y+tile,x:x+tile]+=weight
    return (acc/total[None])[:,:h,:w]


def infer_cover(descriptor, rgb, valid, transform, manifest, payload, output, progress):
    classes=descriptor["classes"]
    channels=descriptor.get("channels",3)
    if channels not in (3,4) or rgb.shape[2] != channels:
        raise ValueError("Input channels do not match the RGB or reviewed-height model.")
    model=cover_model(len(classes),channels)
    model.load_state_dict(torch.load(descriptor["checkpoint_path"],map_location="cpu",weights_only=True),strict=True)
    model.eval(); progress("land-cover inference",.35)
    probability=cover_probabilities(model,rgb.astype(np.float32)/255 if channels==3 else rgb.astype(np.float32))
    confidence=probability.max(axis=0)
    labels=probability.argmax(axis=0).astype(np.uint8)
    labels[~valid | (confidence<payload["threshold"])]=255
    with rasterio.open(output/"land_cover.tif","w",driver="GTiff",width=labels.shape[1],height=labels.shape[0],count=1,
                       dtype="uint8",nodata=255,crs=manifest["analysis_crs"],transform=transform,compress="deflate") as dst:
        dst.write(labels,1)
    features=[]
    for geom, value in shapes(labels,mask=(labels!=0)&(labels!=255),transform=transform,connectivity=4):
        value=int(value); poly=shape(geom)
        if poly.area<payload["min_area_m2"]: continue
        mask=rasterize([(poly,1)],out_shape=labels.shape,transform=transform).astype(bool)
        name=classes[value]
        features.append({"type":"Feature","geometry":mapping(to_wgs(poly,manifest["analysis_crs"])),"properties":{
            "source_id":len(features)+1,"class_name":name,"feature_type":"road_surface" if name=="road" else "land_cover",
            "functional_use":"unknown","score":float(probability[value][mask].mean()),"flags":"unvalidated_model"}})
    if "road" in classes:
        from .roads import centerlines
        roads=centerlines(labels==classes.index("road"),transform,manifest["analysis_crs"])
        (output/"road_centerlines_wgs84.geojson").write_text(json.dumps(roads,allow_nan=False))
    progress("vectorizing cover and roads",.75)
    return {"type":"FeatureCollection","features":features},"land_cover_segmentation"
