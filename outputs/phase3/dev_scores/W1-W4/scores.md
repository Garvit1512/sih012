# Building detection scores (32 approved labels, 4 windows)

Eval grid 0.1 m; building match IoU >= 0.5; pieces < 2.0 m^2 dropped.

| run | pixel_precision | pixel_recall | pixel_iou | building_precision | building_recall | matched | n_pred | n_label | mean_matched_iou |
|---|---|---|---|---|---|---|---|---|---|
| A_whu_baseline | 0.970 | 0.845 | 0.824 | 0.583 | 0.219 | 7 | 12 | 32 | 0.749 |
| B_approved_postproc | 0.970 | 0.845 | 0.824 | 0.643 | 0.281 | 9 | 14 | 32 | 0.773 |
| C_maskrcnn_0.6m | 0.934 | 0.612 | 0.587 | 0.556 | 0.312 | 10 | 18 | 32 | 0.693 |
| C_maskrcnn_0.3m | 0.952 | 0.367 | 0.360 | 0.765 | 0.406 | 13 | 17 | 32 | 0.801 |

## Per window

- A_whu_baseline / W1_dense_informal: pixel_precision=0.936, pixel_recall=0.781, pixel_iou=0.741, building_precision=0.333, building_recall=0.125, matched=2, n_pred=6, n_label=16, mean_matched_iou=0.608
- A_whu_baseline / W2_formal_residential: pixel_precision=0.965, pixel_recall=0.898, pixel_iou=0.870, building_precision=1.000, building_recall=0.667, matched=2, n_pred=2, n_label=3, mean_matched_iou=0.865
- A_whu_baseline / W3_large_sheds: pixel_precision=0.991, pixel_recall=0.843, pixel_iou=0.837, building_precision=0.500, building_recall=0.091, matched=1, n_pred=2, n_label=11, mean_matched_iou=0.588
- A_whu_baseline / W4_vegetation: pixel_precision=0.969, pixel_recall=0.932, pixel_iou=0.904, building_precision=1.000, building_recall=1.000, matched=2, n_pred=2, n_label=2, mean_matched_iou=0.854
- B_approved_postproc / W1_dense_informal: pixel_precision=0.936, pixel_recall=0.781, pixel_iou=0.741, building_precision=0.429, building_recall=0.188, matched=3, n_pred=7, n_label=16, mean_matched_iou=0.711
- B_approved_postproc / W2_formal_residential: pixel_precision=0.965, pixel_recall=0.898, pixel_iou=0.870, building_precision=1.000, building_recall=0.667, matched=2, n_pred=2, n_label=3, mean_matched_iou=0.865
- B_approved_postproc / W3_large_sheds: pixel_precision=0.991, pixel_recall=0.843, pixel_iou=0.837, building_precision=0.667, building_recall=0.182, matched=2, n_pred=3, n_label=11, mean_matched_iou=0.694
- B_approved_postproc / W4_vegetation: pixel_precision=0.969, pixel_recall=0.932, pixel_iou=0.904, building_precision=1.000, building_recall=1.000, matched=2, n_pred=2, n_label=2, mean_matched_iou=0.854
- C_maskrcnn_0.6m / W1_dense_informal: pixel_precision=0.877, pixel_recall=0.425, pixel_iou=0.401, building_precision=0.333, building_recall=0.125, matched=2, n_pred=6, n_label=16, mean_matched_iou=0.559
- C_maskrcnn_0.6m / W2_formal_residential: pixel_precision=0.875, pixel_recall=0.916, pixel_iou=0.810, building_precision=1.000, building_recall=1.000, matched=3, n_pred=3, n_label=3, mean_matched_iou=0.730
- C_maskrcnn_0.6m / W3_large_sheds: pixel_precision=0.980, pixel_recall=0.541, pixel_iou=0.535, building_precision=0.600, building_recall=0.273, matched=3, n_pred=5, n_label=11, mean_matched_iou=0.607
- C_maskrcnn_0.6m / W4_vegetation: pixel_precision=0.974, pixel_recall=0.941, pixel_iou=0.918, building_precision=0.500, building_recall=1.000, matched=2, n_pred=4, n_label=2, mean_matched_iou=0.903
- C_maskrcnn_0.3m / W1_dense_informal: pixel_precision=0.965, pixel_recall=0.598, pixel_iou=0.585, building_precision=0.875, building_recall=0.438, matched=7, n_pred=8, n_label=16, mean_matched_iou=0.812
- C_maskrcnn_0.3m / W2_formal_residential: pixel_precision=0.894, pixel_recall=0.677, pixel_iou=0.627, building_precision=0.250, building_recall=0.333, matched=1, n_pred=4, n_label=3, mean_matched_iou=0.886
- C_maskrcnn_0.3m / W3_large_sheds: pixel_precision=0.976, pixel_recall=0.050, pixel_iou=0.050, building_precision=1.000, building_recall=0.273, matched=3, n_pred=3, n_label=11, mean_matched_iou=0.730
- C_maskrcnn_0.3m / W4_vegetation: pixel_precision=0.999, pixel_recall=0.796, pixel_iou=0.796, building_precision=1.000, building_recall=1.000, matched=2, n_pred=2, n_label=2, mean_matched_iou=0.826
