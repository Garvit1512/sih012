# Building detection scores (30 approved labels, 4 windows)

Eval grid 0.1 m; building match IoU >= 0.5; pieces < 2.0 m^2 dropped.

| run | pixel_precision | pixel_recall | pixel_iou | building_precision | building_recall | matched | n_pred | n_label | mean_matched_iou |
|---|---|---|---|---|---|---|---|---|---|
| A_whu_baseline | 0.835 | 0.645 | 0.572 | 0.429 | 0.200 | 6 | 14 | 30 | 0.707 |
| B_approved_postproc | 0.840 | 0.616 | 0.551 | 0.421 | 0.267 | 8 | 19 | 30 | 0.728 |
| C_maskrcnn_0.3m | 0.958 | 0.569 | 0.555 | 0.632 | 0.400 | 12 | 19 | 30 | 0.809 |
| D_hybrid | 0.874 | 0.642 | 0.588 | 0.368 | 0.233 | 7 | 19 | 30 | 0.688 |

## Secondary reference (Microsoft ML footprints - NOT ground truth)

| source | pixel_precision | pixel_recall | pixel_iou | building_precision | building_recall | matched | n_pred | n_label | mean_matched_iou |
|---|---|---|---|---|---|---|---|---|---|
| microsoft | 0.568 | 0.454 | 0.338 | 0.000 | 0.000 | 0 | 21 | 30 | n/a |

## Per window

- A_whu_baseline / T1_mixed_west: pixel_precision=0.833, pixel_recall=0.701, pixel_iou=0.615, building_precision=0.600, building_recall=0.600, matched=3, n_pred=5, n_label=5, mean_matched_iou=0.780
- A_whu_baseline / T2_dense_south_centre: pixel_precision=0.702, pixel_recall=0.589, pixel_iou=0.472, building_precision=0.200, building_recall=0.067, matched=1, n_pred=5, n_label=15, mean_matched_iou=0.567
- A_whu_baseline / T3_residential_north: pixel_precision=0.969, pixel_recall=0.891, pixel_iou=0.866, building_precision=1.000, building_recall=1.000, matched=1, n_pred=1, n_label=1, mean_matched_iou=0.817
- A_whu_baseline / T4_dense_south_west: pixel_precision=0.934, pixel_recall=0.390, pixel_iou=0.380, building_precision=0.333, building_recall=0.111, matched=1, n_pred=3, n_label=9, mean_matched_iou=0.518
- B_approved_postproc / T1_mixed_west: pixel_precision=0.833, pixel_recall=0.701, pixel_iou=0.615, building_precision=0.500, building_recall=0.600, matched=3, n_pred=6, n_label=5, mean_matched_iou=0.780
- B_approved_postproc / T2_dense_south_centre: pixel_precision=0.697, pixel_recall=0.515, pixel_iou=0.421, building_precision=0.250, building_recall=0.133, matched=2, n_pred=8, n_label=15, mean_matched_iou=0.636
- B_approved_postproc / T3_residential_north: pixel_precision=0.969, pixel_recall=0.891, pixel_iou=0.866, building_precision=1.000, building_recall=1.000, matched=1, n_pred=1, n_label=1, mean_matched_iou=0.817
- B_approved_postproc / T4_dense_south_west: pixel_precision=0.940, pixel_recall=0.381, pixel_iou=0.372, building_precision=0.500, building_recall=0.222, matched=2, n_pred=4, n_label=9, mean_matched_iou=0.697
- C_maskrcnn_0.3m / T1_mixed_west: pixel_precision=0.977, pixel_recall=0.745, pixel_iou=0.732, building_precision=0.600, building_recall=0.600, matched=3, n_pred=5, n_label=5, mean_matched_iou=0.832
- C_maskrcnn_0.3m / T2_dense_south_centre: pixel_precision=0.931, pixel_recall=0.540, pixel_iou=0.520, building_precision=0.833, building_recall=0.333, matched=5, n_pred=6, n_label=15, mean_matched_iou=0.813
- C_maskrcnn_0.3m / T3_residential_north: pixel_precision=0.999, pixel_recall=0.450, pixel_iou=0.450, building_precision=0.000, building_recall=0.000, matched=0, n_pred=2, n_label=1, mean_matched_iou=n/a
- C_maskrcnn_0.3m / T4_dense_south_west: pixel_precision=0.948, pixel_recall=0.636, pixel_iou=0.615, building_precision=0.667, building_recall=0.444, matched=4, n_pred=6, n_label=9, mean_matched_iou=0.787
- D_hybrid / T1_mixed_west: pixel_precision=0.833, pixel_recall=0.701, pixel_iou=0.615, building_precision=0.600, building_recall=0.600, matched=3, n_pred=5, n_label=5, mean_matched_iou=0.780
- D_hybrid / T2_dense_south_centre: pixel_precision=0.791, pixel_recall=0.581, pixel_iou=0.503, building_precision=0.375, building_recall=0.200, matched=3, n_pred=8, n_label=15, mean_matched_iou=0.653
- D_hybrid / T3_residential_north: pixel_precision=0.969, pixel_recall=0.891, pixel_iou=0.866, building_precision=0.000, building_recall=0.000, matched=0, n_pred=2, n_label=1, mean_matched_iou=n/a
- D_hybrid / T4_dense_south_west: pixel_precision=0.934, pixel_recall=0.390, pixel_iou=0.380, building_precision=0.250, building_recall=0.111, matched=1, n_pred=4, n_label=9, mean_matched_iou=0.522
- microsoft_ml_footprints / T1_mixed_west: pixel_precision=0.804, pixel_recall=0.073, pixel_iou=0.072, building_precision=0.000, building_recall=0.000, matched=0, n_pred=1, n_label=5, mean_matched_iou=n/a
- microsoft_ml_footprints / T2_dense_south_centre: pixel_precision=0.455, pixel_recall=0.471, pixel_iou=0.301, building_precision=0.000, building_recall=0.000, matched=0, n_pred=12, n_label=15, mean_matched_iou=n/a
- microsoft_ml_footprints / T3_residential_north: pixel_precision=0.730, pixel_recall=0.653, pixel_iou=0.526, building_precision=0.000, building_recall=0.000, matched=0, n_pred=3, n_label=1, mean_matched_iou=n/a
- microsoft_ml_footprints / T4_dense_south_west: pixel_precision=0.576, pixel_recall=0.461, pixel_iou=0.344, building_precision=0.000, building_recall=0.000, matched=0, n_pred=5, n_label=9, mean_matched_iou=n/a
