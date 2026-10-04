# Building detection scores (49 approved labels, 4 windows)

Eval grid 0.1 m; building match IoU >= 0.5; pieces < 2.0 m^2 dropped.

| run | pixel_precision | pixel_recall | pixel_iou | building_precision | building_recall | matched | n_pred | n_label | mean_matched_iou |
|---|---|---|---|---|---|---|---|---|---|
| A_whu_baseline | 0.966 | 0.817 | 0.794 | 0.600 | 0.184 | 9 | 15 | 49 | 0.722 |
| B_approved_postproc | 0.966 | 0.817 | 0.794 | 0.684 | 0.265 | 13 | 19 | 49 | 0.730 |
| C_maskrcnn_0.3m | 0.973 | 0.453 | 0.447 | 0.870 | 0.408 | 20 | 23 | 49 | 0.778 |
| D_hybrid | 0.967 | 0.804 | 0.782 | 0.696 | 0.327 | 16 | 23 | 49 | 0.724 |

## Per window

- A_whu_baseline / V1_dense_informal: pixel_precision=0.950, pixel_recall=0.859, pixel_iou=0.821, building_precision=0.000, building_recall=0.000, matched=0, n_pred=3, n_label=13, mean_matched_iou=n/a
- A_whu_baseline / V2_large_sheds: pixel_precision=0.981, pixel_recall=0.981, pixel_iou=0.962, building_precision=0.000, building_recall=0.000, matched=0, n_pred=1, n_label=10, mean_matched_iou=n/a
- A_whu_baseline / V3_residential: pixel_precision=0.945, pixel_recall=0.599, pixel_iou=0.579, building_precision=1.000, building_recall=0.438, matched=7, n_pred=7, n_label=16, mean_matched_iou=0.742
- A_whu_baseline / V4_small_roofs: pixel_precision=0.963, pixel_recall=0.640, pixel_iou=0.625, building_precision=0.500, building_recall=0.200, matched=2, n_pred=4, n_label=10, mean_matched_iou=0.650
- B_approved_postproc / V1_dense_informal: pixel_precision=0.950, pixel_recall=0.859, pixel_iou=0.821, building_precision=0.000, building_recall=0.000, matched=0, n_pred=3, n_label=13, mean_matched_iou=n/a
- B_approved_postproc / V2_large_sheds: pixel_precision=0.981, pixel_recall=0.981, pixel_iou=0.962, building_precision=0.000, building_recall=0.000, matched=0, n_pred=2, n_label=10, mean_matched_iou=n/a
- B_approved_postproc / V3_residential: pixel_precision=0.945, pixel_recall=0.599, pixel_iou=0.579, building_precision=0.875, building_recall=0.438, matched=7, n_pred=8, n_label=16, mean_matched_iou=0.760
- B_approved_postproc / V4_small_roofs: pixel_precision=0.963, pixel_recall=0.640, pixel_iou=0.624, building_precision=1.000, building_recall=0.600, matched=6, n_pred=6, n_label=10, mean_matched_iou=0.694
- C_maskrcnn_0.3m / V1_dense_informal: pixel_precision=0.963, pixel_recall=0.553, pixel_iou=0.542, building_precision=1.000, building_recall=0.462, matched=6, n_pred=6, n_label=13, mean_matched_iou=0.771
- C_maskrcnn_0.3m / V2_large_sheds: pixel_precision=0.985, pixel_recall=0.272, pixel_iou=0.271, building_precision=0.400, building_recall=0.200, matched=2, n_pred=5, n_label=10, mean_matched_iou=0.613
- C_maskrcnn_0.3m / V3_residential: pixel_precision=0.982, pixel_recall=0.671, pixel_iou=0.663, building_precision=1.000, building_recall=0.438, matched=7, n_pred=7, n_label=16, mean_matched_iou=0.802
- C_maskrcnn_0.3m / V4_small_roofs: pixel_precision=0.935, pixel_recall=0.471, pixel_iou=0.456, building_precision=1.000, building_recall=0.500, matched=5, n_pred=5, n_label=10, mean_matched_iou=0.817
- D_hybrid / V1_dense_informal: pixel_precision=0.949, pixel_recall=0.796, pixel_iou=0.763, building_precision=0.667, building_recall=0.308, matched=4, n_pred=6, n_label=13, mean_matched_iou=0.695
- D_hybrid / V2_large_sheds: pixel_precision=0.981, pixel_recall=0.980, pixel_iou=0.962, building_precision=0.200, building_recall=0.100, matched=1, n_pred=5, n_label=10, mean_matched_iou=0.613
- D_hybrid / V3_residential: pixel_precision=0.950, pixel_recall=0.592, pixel_iou=0.574, building_precision=1.000, building_recall=0.438, matched=7, n_pred=7, n_label=16, mean_matched_iou=0.753
- D_hybrid / V4_small_roofs: pixel_precision=0.963, pixel_recall=0.640, pixel_iou=0.625, building_precision=0.800, building_recall=0.400, matched=4, n_pred=5, n_label=10, mean_matched_iou=0.729
