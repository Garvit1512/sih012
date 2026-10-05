"""A separate, evidence-labelled functional-use baseline with an explicit unknown result.

This is a local research candidate. Cover predictions never create use labels.
Inputs are reviewed tabular attributes with source evidence and spatial splits.
"""
import numpy as np

FEATURES = ("area_m2", "compactness", "distance_to_road_m", "neighbours_within_50m")


def attributes(buildings, roads, analysis_crs):
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    from ..utils.crs import metric_crs, to_utm
    metric_crs(analysis_crs)
    geometries=[to_utm(shape(f["geometry"]),analysis_crs=analysis_crs) for f in buildings]
    road=unary_union([to_utm(shape(f["geometry"]),analysis_crs=analysis_crs) for f in roads])
    centers=[g.centroid for g in geometries]; tree=STRtree(centers); rows=[]
    for i,g in enumerate(geometries):
        rows.append({"feature_id":buildings[i].get("id"),"area_m2":float(g.area),
                     "compactness":float(4*np.pi*g.area/g.length**2) if g.length else None,
                     "distance_to_road_m":float(g.distance(road)) if not road.is_empty else None,
                     "neighbours_within_50m":len(tree.query(centers[i],predicate="dwithin",distance=50))-1,
                     "functional_use":"unknown","note":"Geometric attributes; no ownership or use inferred."})
    return rows


def fit(rows, max_distance=3.0):
    """Fit a nearest-centroid baseline using train rows only; freeze rejection distance before evaluation."""
    groups={}; training=[]
    for row in rows:
        group=(row["site_id"],row["block_id"])
        if group in groups and groups[group]!=row["split"]: raise ValueError("Functional-use spatial block leaks across splits.")
        groups[group]=row["split"]
        if row["split"]=="train":
            if row.get("reviewed") is not True or not row.get("evidence_ids") or row.get("use_label") in (None,"unknown"):
                raise ValueError("Use training labels need reviewed source evidence.")
            training.append(row)
    names=sorted({r["use_label"] for r in training})
    if len(names)<2 or any(sum(r["use_label"]==name for r in training)<2 for name in names):
        raise ValueError("Provide at least two reviewed train examples per use class.")
    x=np.array([[r[f] for f in FEATURES] for r in training],dtype=float)
    if not np.isfinite(x).all() or max_distance<=0: raise ValueError("Use finite attributes and a positive rejection distance.")
    mean=x.mean(axis=0); scale=x.std(axis=0); scale[scale<1e-6]=1
    normalized=(x-mean)/scale
    centers={name:normalized[[r["use_label"]==name for r in training]].mean(axis=0).tolist() for name in names}
    return {"model":"nearest_centroid_use_candidate","features":list(FEATURES),"mean":mean.tolist(),"scale":scale.tolist(),
            "centers":centers,"max_distance":max_distance,"training_evidence_ids":sorted({str(e) for r in training for e in r["evidence_ids"]}),
            "status":"candidate","accuracy_status":"separate fresh functional-use validation required"}


def predict(model, row):
    try: x=np.array([row[f] for f in model["features"]],dtype=float)
    except (KeyError,ValueError,TypeError): return {"functional_use_suggestion":"unknown","reason":"missing attributes"}
    if not np.isfinite(x).all(): return {"functional_use_suggestion":"unknown","reason":"missing attributes"}
    z=(x-np.array(model["mean"]))/np.array(model["scale"])
    distances=sorted((float(np.linalg.norm(z-np.array(center))),name) for name,center in model["centers"].items())
    distance,name=distances[0]
    ambiguous=len(distances)>1 and abs(distances[1][0]-distance)<model.get("ambiguity_margin",.1)
    suggestion=name if distance<=model["max_distance"] and not ambiguous else "unknown"
    return {"functional_use_suggestion":suggestion,"distance":distance,"calibrated_probability":None,
            "functional_use":"unknown","reason":"candidate requires evidence review" if suggestion!="unknown" else "outside training support or ambiguous"}
