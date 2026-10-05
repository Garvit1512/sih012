"""Portable application setup, import and preservation checks."""
import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

from .config import ROOT, Settings
from .services.ingestion_service import ingest_site, sha256
from .services.model_service import models
from .services.site_service import SiteRegistry


def doctor(settings=None):
    settings=settings or Settings()
    packages={}; missing=[]
    for name in ("fastapi","uvicorn","rasterio","shapely","pyproj","geopandas","numpy","pillow","python-multipart"):
        try: packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: missing.append(name)
    return {"ready":sys.version_info[:2]>=(3,12) and not missing,"python":sys.version.split()[0],
            "packages":packages,"missing_runtime":missing,"sites":len(SiteRegistry(settings).all()),
            "models":models(settings.models_dir),"storage":{"sites":str(settings.sites_dir),"jobs":str(settings.jobs_dir),
                                         "workspace":str(settings.workspace_dir),"exports":str(settings.export_root)},
            "mode":"local single process; launch uvicorn with one worker",
            "note":"The app starts with zero sites. Data acquisition and real-reference model validation are separate."}


def check_frozen(root=ROOT):
    reports=[]
    inventories=("outputs/instance_study/frozen_hashes.sha256","outputs/phase3/frozen_hashes.sha256",
                 "outputs/phase3/protocol_lock.sha256","outputs/phase3/protocol_lock_amend1.sha256")
    for relative in inventories:
        inventory=root/relative; checked=0; missing=[]; changed=[]
        for line in inventory.read_text().splitlines():
            if not line.strip(): continue
            expected,path=line.split(maxsplit=1); path=path.lstrip("*"); target=root/path
            if not target.is_file(): missing.append(path); continue
            checked+=1
            if sha256(target)!=expected: changed.append(path)
        reports.append({"inventory":relative,"checked":checked,"changed":changed,"missing":missing})
    return {"present_artifacts_unchanged":not any(r["changed"] for r in reports),
            "all_assets_available":not any(r["missing"] for r in reports),"inventories":reports}


def main():
    parser=argparse.ArgumentParser(description=__doc__); commands=parser.add_subparsers(dest="command",required=True)
    commands.add_parser("doctor"); commands.add_parser("check-frozen")
    serve=commands.add_parser("serve"); serve.add_argument("--port",type=int,default=8765)
    ingest=commands.add_parser("import-site"); ingest.add_argument("image",type=Path); ingest.add_argument("site_id")
    ingest.add_argument("--name",required=True); ingest.add_argument("--analysis-crs"); ingest.add_argument("--licence",default="unknown")
    ingest.add_argument("--source-url",default=""); ingest.add_argument("--dsm",type=Path); ingest.add_argument("--dtm",type=Path)
    ingest.add_argument("--vertical-datum")
    args=parser.parse_args(); settings=Settings()
    if args.command=="serve":
        import uvicorn
        uvicorn.run("backend.main:app",host="127.0.0.1",port=args.port,workers=1)
    elif args.command=="import-site":
        result=ingest_site(settings,args.image,args.site_id,args.name,args.analysis_crs,args.licence,args.source_url,
                           {k:v for k,v in (("dsm",args.dsm),("dtm",args.dtm)) if v},args.vertical_datum)
        print(json.dumps(result,indent=2))
    else:
        report=doctor(settings) if args.command=="doctor" else check_frozen()
        print(json.dumps(report,indent=2))
        if not report.get("ready",report.get("present_artifacts_unchanged",False)): sys.exit(1)


if __name__=="__main__": main()
