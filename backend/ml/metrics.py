"""Scores for new experiments; historical scoring scripts are left intact."""
import numpy as np
from scipy.optimize import linear_sum_assignment


def confusion(reference, prediction, classes, ignore=255):
    reference, prediction = np.asarray(reference), np.asarray(prediction)
    if reference.shape != prediction.shape: raise ValueError("Prediction/reference grids differ.")
    valid = reference != ignore
    if ((reference[valid] < 0) | (reference[valid] >= classes)).any() or ((prediction[valid] < 0) | (prediction[valid] >= classes)).any():
        raise ValueError("Class ID outside the schema.")
    return np.bincount((reference[valid] * classes + prediction[valid]).ravel(), minlength=classes**2).reshape(classes, classes)


def class_scores(matrix, names):
    matrix = np.asarray(matrix, dtype=np.int64)
    rows = []
    for i, name in enumerate(names):
        tp = int(matrix[i,i]); actual = int(matrix[i,:].sum()); predicted = int(matrix[:,i].sum())
        union = actual + predicted - tp
        rows.append({"class": name, "reference_pixels": actual, "predicted_pixels": predicted, "tp": tp,
                     "iou": tp / union if union else None,
                     "precision": tp / predicted if predicted else None, "recall": tp / actual if actual else None,
                     "f1": 2*tp/(actual+predicted) if actual+predicted else None})
    available = [r["iou"] for r in rows if r["iou"] is not None]
    return {"confusion_matrix": matrix.tolist(), "classes": rows, "macro_iou": float(np.mean(available)) if available else None,
            "evaluated_pixels": int(matrix.sum())}


def instance_scores(reference, prediction, match_iou=0.5):
    if not 0 < match_iou <= 1: raise ValueError("Match IoU must be in (0,1].")
    ref, pred = [g for g in reference if not g.is_empty], [g for g in prediction if not g.is_empty]
    ious = np.zeros((len(ref), len(pred)))
    for i, a in enumerate(ref):
        for j, b in enumerate(pred):
            union = a.union(b).area
            ious[i,j] = a.intersection(b).area / union if union else 0
    # Maximize the count of eligible matches first, then IoU among those matches.
    eligible = ious >= match_iou
    if ious.size:
        r, p = linear_sum_assignment(-(eligible.astype(float) * (min(ious.shape) + 1) + ious * eligible))
        matches = [(int(i), int(j), float(ious[i,j])) for i,j in zip(r,p) if eligible[i,j]]
    else: matches = []
    tp, fp, fn = len(matches), len(pred)-len(matches), len(ref)-len(matches)
    return {"tp":tp,"fp":fp,"fn":fn,"reference_count":len(ref),"prediction_count":len(pred),
            "precision":tp/(tp+fp) if tp+fp else None,"recall":tp/(tp+fn) if tp+fn else None,
            "f1":2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,"matches":matches,
            "merge_candidates":int((eligible.sum(axis=0)>1).sum()),"split_candidates":int((eligible.sum(axis=1)>1).sum())}


def boundary_scores(reference, prediction, tolerance_m):
    if tolerance_m <= 0: raise ValueError("Boundary tolerance must be positive metres.")
    r, p = reference.boundary, prediction.boundary
    from shapely.geometry import LineString
    if r is None: r = LineString()
    if p is None: p = LineString()
    precision = p.intersection(r.buffer(tolerance_m)).length/p.length if p.length else None
    recall = r.intersection(p.buffer(tolerance_m)).length/r.length if r.length else None
    return {"precision":precision,"recall":recall,"tolerance_m":tolerance_m,
            "hausdorff_distance_m":r.hausdorff_distance(p) if r.length and p.length else None}
