# 复杂位图：柱体/区间线与曲线身份候选

两个模块已接入固定入口，仍为试验方法，不默认替代已有清晰图的读取器。图例、统计量、零线、面板与系列由Agent按原文确认；程序判断的是几何候选，不保证自动理解研究身份。输出统一接inspect/edit/undo，不另建纠错或写表流程。

## 柱体、有帽区间与无帽区间

固定方法`bitmap_bar_ranges`，脚本`tools/complex_bars.py`。使用现有NumPy/Pillow和OpenCV形态学；OpenCV是可选依赖，见requirements-complex.txt，不在任务运行中自动安装。

```json
{
  "sources":{"s":{"image":"source.png"}},
  "panels":{"p":{"source":"s","bounds":[1,1,198,198],"zero_y":90,
    "axis":{"coordinate":"y","ticks":[[40,50],[90,0],[140,-50]],"unit":"%"}}},
  "bars":[{"id":"bar1","panel":"p","identity":{"series":"2020s","statistic":"median"},
    "x":45,"roi":[35,10,55,180],"range_expected":true}],
  "capped_ranges":[{"id":"base1","panel":"p","identity":{"statistic":"baseline ensemble range"},
    "roi":[10,10,30,180],"crosses_zero":true}]
}
```

每根柱提供已经确认的x位置与槽位ROI，不默认等距或按大小排系列；零线须明确。槽位应包含自身边框，避免截掉网纹柱外边缘或包含相邻柱。可用selector选择灰度/颜色及exclude_boxes排除图例。`range_expected=false`只用于原图确实不含区间线的柱，不以此隐藏未读端点。

程序分开处理：

- 柱体要求中心线两侧都有墨迹，避免把单根竖线当柱。网纹柱的连通判断使用小范围闭运算；这只影响柱体候选，端点与横帽始终读原始掩膜。近零、两侧都有柱体或未找到时保留歧义/空候选，不填0。
- 竖线按原始墨迹段检测，横帽用水平形态学检测并检查是否跨过竖线；相邻描边列合并为同一几何候选。重复配对保留，不任取一条。
- 有帽区间要求两端均有横帽；若原文说明基准范围跨零，可显式要求crosses_zero。缺帽、相邻柱边框或图例装饰可能造成歧义，不能仅凭形状自动指定统计含义。
- 无帽竖线与已分配柱的中心/柱体相交关系用于配对；端点与柱顶/零线重合，或端点检测到横帽时留空并送核对。

输出角色：bar_tip、uncapped_top/bottom、capped_top/bottom。**top/bottom是图像y坐标的上/下端，不是数值大小顺序**；geometry.json另存标定后的候选值。语义上的基准/未来范围及分位数必须沿用图注。A2图10(a)(b)与(c)不能混套区间定义。

## 密集、交叉、重叠与虚线曲线

固定方法`bitmap_curve_identity`，脚本`tools/complex_curves.py`。每个系列明确选择器、至少两个锚点、线宽范围与实/虚线；不会按纵向高低重命名情景。

```json
{
  "sources":{"s":{"image":"source.png"}},
  "panels":{"p":{"source":"s","bounds":[1,1,198,198],
    "axis":{"coordinate":"y","ticks":[[20,100],[180,0]],"unit":"Ml/d"}}},
  "traces":[{"id":"curve1","panel":"p","identity":{"series":"A2 2080s"},
    "roi":[5,5,190,190],"anchors":[[10,40],[180,150]],"sample_x":[10,50,100,150,180],
    "selector":{"mode":"gray","range":[0,180]},
    "style":{"pattern":"dashed","width_range":[1,3],"max_gap_px":8},
    "beam_width":32,"max_step_px":10,"anchor_tolerance_px":2,"slope_weight":1.5,"ambiguity_cost":1.0}]
}
```

带上下文的线宽、颜色与线型仍需原图图例确认；锚点不可直接抄待验证位置的答案。ROI最多300000像素，beam最多128，gap最多30像素。形态超出范围时拆面板，不无限扩大搜索。

- 有界多假设搜索结合斜率变化、线宽及锚点约束保留备选。宽度惩罚和路径成本不是置信概率；最平滑路径不一定是目标曲线。
- 实线附近有可用墨迹时不为降低成本主动跳过；虚线允许有限断口，但断口保持缺值，不能把预测连线作为读图值。虚线身份需要源图中有断口支持，算法自行跳过墨迹不算依据。
- 多系列轨迹接近/共享同一墨迹时进入身份核对；输出重合候选不能证明实际数值相等，完全遮挡处不能恢复隐藏差异。
- 同一列远处还有其他曲线，不会单独触发复核；附近分支、线宽冲突、共享墨迹、多个路径和缺口仍标记。无告警也不是已核验。

## 运行与证据

```text
python tools/complex_bars.py job.json 新输出目录
python tools/complex_curves.py job.json 新输出目录
```

日常在task.json的part选择相应固定方法，通过run_task的trial→确认→run→inspect/edit/undo。生成器可使用已有job/trial_job对象，不声称已提供这两种方法的紧凑参数模板。旧有清晰实线仍优先原bitmap_lines；不要因增加新模块就重做已有效的取点。

共同输出source_job.json、来源图摘要、geometry.json、review_job.json、检查叠图和短摘要。缺值/多候选按原接口拦截保存；图义、单位和出处仍要核对。看图属于同一Agent自检，程序通过不代表整图完成。

验证：tests/test_complex_geometry.py用已知合成图检验正负柱、薄竖线与柱体区别、帽线配对、区间位于零线一侧、交叉/共享墨迹、虚线缺口及固定入口。真实A2试验仅产生候选，未写研究底稿。局部可用性不代表全图正确率；图10仍有网纹/配对缺口，图11试验只覆盖5/13个绘图系列组。

OpenCV复用依据：[官方形态学分离水平/竖直线教程](https://docs.opencv.org/4.13.0/dd/dd7/tutorial_morph_lines_detection.html)。本项目几何配对和多假设搜索为原有工具的新增实现，不宣称调用了第三方完整图表理解模型。
