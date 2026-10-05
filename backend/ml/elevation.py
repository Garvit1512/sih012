"""Build optional nDSM only after a reviewer supplies datum/unit/registration evidence."""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio

from ..services.ingestion_service import sha256
from ..services.job_service import atomic_json, now


def prepare_height(site_dir, vertical_datum, reviewer, evidence, registration_reviewed=False, units="metres"):
    site_dir=Path(site_dir); path=site_dir/"manifest.json"; manifest=json.loads(path.read_text())
    if manifest.get("schema_version",1)<2: raise ValueError("Preserve the historical site; use a new imported site.")
    if not registration_reviewed or units!="metres" or not all((vertical_datum.strip(),reviewer.strip(),evidence.strip())):
        raise ValueError("Height requires reviewed registration, metre units, a datum, a reviewer and source evidence.")
    elevation=manifest.get("elevation",{})
    if not all(k in elevation for k in ("dsm","dtm")): raise ValueError("Both DSM and DTM are required for nDSM.")
    if any(sha256(site_dir/elevation[k]["file"])!=elevation[k]["sha256"] for k in ("dsm","dtm")):
        raise ValueError("Elevation source differs from its imported hash.")
    output=site_dir/"ndsm.tif"
    if output.exists(): raise ValueError("Refusing to overwrite nDSM; create a new site for changed elevation.")
    with rasterio.open(site_dir/elevation["dsm"]["file"]) as dsm, rasterio.open(site_dir/elevation["dtm"]["file"]) as dtm, rasterio.open(site_dir/"source.tif") as rgb:
        if any(ds.crs!=rgb.crs or ds.transform!=rgb.transform or ds.shape!=rgb.shape for ds in (dsm,dtm)):
            raise ValueError("DSM/DTM must be aligned with RGB; registration review cannot override a grid mismatch.")
        z=dsm.read(1).astype(np.float32)-dtm.read(1).astype(np.float32)
        valid=(dsm.dataset_mask()>0)&(dtm.dataset_mask()>0)&(rgb.dataset_mask()>0)&np.isfinite(z)
        if not valid.any(): raise ValueError("Elevation has no valid pixels in the imagery.")
        z[~valid]=-9999
        profile=dsm.profile; profile.update(dtype="float32",nodata=-9999,count=1,compress="deflate")
        with rasterio.open(output,"w",**profile) as dst: dst.write(z,1)
    manifest["elevation"]["ndsm"]={"file":output.name,"sha256":sha256(output),"vertical_units":"metres",
        "vertical_datum":vertical_datum,"registration_reviewed":True,"reviewer":reviewer,"evidence":evidence,
        "reviewed_at":now(),"valid_pixels":int(valid.sum()),"range_m":[float(z[valid].min()),float(z[valid].max())],
        "status":"reviewer-asserted source metadata; independent vertical accuracy not established"}
    atomic_json(path,manifest)
    return manifest["elevation"]["ndsm"]


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site",type=Path); parser.add_argument("--vertical-datum",required=True)
    parser.add_argument("--reviewer",required=True); parser.add_argument("--evidence",required=True)
    parser.add_argument("--registration-reviewed",action="store_true")
    args=parser.parse_args(); print(json.dumps(prepare_height(args.site,args.vertical_datum,args.reviewer,args.evidence,args.registration_reviewed),indent=2))
