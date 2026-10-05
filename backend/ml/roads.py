"""Surface-derived centerlines and connectivity diagnostics, not surveyed access rights."""
import numpy as np
from shapely.geometry import LineString, mapping

from ..utils.crs import to_wgs


def centerlines(surface, transform, crs):
    from skimage.morphology import skeletonize
    positions=np.argwhere(skeletonize(np.asarray(surface,dtype=bool)))
    if len(positions)>100000: raise ValueError("Road skeleton exceeds the local graph limit; use a smaller pilot area.")
    pixels=set(map(tuple,positions))
    graph={p:sorted(q for dy in (-1,0,1) for dx in (-1,0,1)
                    if (dy or dx) and (q:=(p[0]+dy,p[1]+dx)) in pixels) for p in pixels}
    seen=set(); features=[]
    # Start with endpoints/junctions, then cover any isolated cycles.
    order=sorted(graph,key=lambda p:(len(graph[p])==2,p))
    for start in order:
        for second in graph[start]:
            edge=frozenset((start,second))
            if edge in seen: continue
            points=[start,second]; seen.add(edge); previous,current=start,second
            while len(graph[current])==2:
                other=next(p for p in graph[current] if p!=previous)
                edge=frozenset((current,other))
                if edge in seen: break
                seen.add(edge); points.append(other); previous,current=current,other
            xy=[transform @ (float(x)+.5,float(y)+.5) for y,x in points]
            line=LineString(xy)
            features.append({"type":"Feature","geometry":mapping(to_wgs(line,crs)),"properties":{
                "source_id":len(features)+1,"feature_type":"road_centerline","length_m":round(line.length,3),
                "source":"skeleton of predicted road surface","access_status":"unverified"}})
    components=0; visited=set()
    for pixel in graph:
        if pixel in visited: continue
        components+=1; pending=[pixel]
        while pending:
            point=pending.pop()
            if point in visited: continue
            visited.add(point); pending.extend(graph[point])
    return {"type":"FeatureCollection","features":features,"diagnostics":{
        "components":components,"endpoints":sum(len(n)==1 for n in graph.values()),
        "junction_pixels":sum(len(n)>2 for n in graph.values()),"skeleton_pixels":len(pixels),
        "note":"Pixel-graph diagnostics; junction pixels may form clusters. Connectivity accuracy needs road references."}}


def centerline_scores(reference, prediction, transform, crs, tolerance_m=1.0):
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from ..utils.crs import to_utm
    if tolerance_m<=0: raise ValueError("Road tolerance must be positive metres.")
    ref,pred=centerlines(reference,transform,crs),centerlines(prediction,transform,crs)
    r=unary_union([to_utm(shape(f["geometry"]),analysis_crs=crs) for f in ref["features"]])
    p=unary_union([to_utm(shape(f["geometry"]),analysis_crs=crs) for f in pred["features"]])
    return {"precision":p.intersection(r.buffer(tolerance_m)).length/p.length if p.length else None,
            "recall":r.intersection(p.buffer(tolerance_m)).length/r.length if r.length else None,
            "reference_components":ref["diagnostics"]["components"],"prediction_components":pred["diagnostics"]["components"],
            "tolerance_m":tolerance_m,"reference_kind":"centerlines derived from reviewed road-surface masks",
            "note":"Component counts are diagnostics; independent route-connectivity and access-rights review remains required."}
