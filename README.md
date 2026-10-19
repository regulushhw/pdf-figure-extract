# pdf-figure-extract

**状态：实验性Skill，持续改进中。** 支持范围和验证边界见下文；不保证任意论文都可自动提取。

面向Agent的论文图表数值化工具：提取数值，保存标定与出处，生成校核叠加图，并检查Excel写入是否一致。执行入口见 [SKILL.md](SKILL.md)。

## 当前支持与限制

- 折线可用显式颜色、线宽与线型绑定同色系列；网格束拒绝、面板边框排除。单条填充四边形虚线另有专用读取器，仍须显式选择；线型不能自动证明情景身份。
- 盘点PDF候选对象；读取部分矢量柱图、直线段曲线和对称点标记。`scan_pages`区分全部`drawing_count`与筛选后的`long_paths`，报告位图原始尺寸、显示分辨率估计和覆盖率；空长路径清单不表示无矢量，页面有位图也不能排除混合图。数字附近的控制字符会提示回看印刷符号，不自动改负号。显示分辨率按轴对齐包围框估计，旋转/变形图片只作参考。
- 自动标定目前限等距线性刻度；对数轴、Bezier路径、堆叠/浮动柱和复杂位图需另用方法。
- 输出保留原数据字段，并增加统一`_evidence`，包括PDF摘要、页面、方法、标定和逐点坐标。原文图号与研究条件由job明确提供，工具不能自行理解论文条件。
- `build_workbook.py`是版式辅助函数；它不会自动生成全部说明、依据或完成视觉检查。
- `verify.py`核对声明数值区与JSON是否一致，不证明原图读得正确。两项工具同时只读检查整本工作簿的公式缓存，拒绝空缓存或Excel错误；缓存可读不证明运算正确或结果最新。检查一次完整覆盖，另做原图检查和保存预览，不用重复运行冒充独立核验。`audit`的`inventory`按原图清单发现整条曲线遗漏、身份错配、重复与点数不足；未列入契约的系列仍不会自动被发现。

## 运行

安装依赖见 `requirements.txt`。在调用方指定的工作目录运行：

```sh
python /path/to/tools/scan_pages.py paper.pdf --json scan.json
python /path/to/tools/extract_bars.py job.json
python /path/to/tools/verify.py workbook.xlsx checks.json
python /path/to/tools/audit_workbook.py workbook.xlsx audit.json
python /path/to/tests/test_regressions.py
```

柱图示例见 `examples/job_bars.example.json`；实际槽位边界、图例映射、单位和时段须逐图确认。输出前缀指向工作区，原始PDF只读；已有文件修改前备份。

## 同色曲线与整条遗漏检查

折线job可用以下字段替换`color_map`（二者互斥）。颜色、线宽和线型须与原图图例核对，不能依数值大小猜情景；线宽单位为PDF pt。

```json
{"series_styles":[
 {"name":"control","color":"0,0,0","width_range":[0.39,0.41],"line_style":"solid"},
 {"name":"future","color":"0,0,0","width_range":[0.79,0.81],"line_style":"dashed"}
]}
```

以下是`audit.json`中的一条规则示例。A列存系列身份，B/C列存两个读数；清单必须先从原图建立，不能只列底稿已有的系列。实际可组合多个身份列，如对象、情景、指标、出处。数据行范围须覆盖完整目标区；分区表头只在无数值且不匹配预期键时跳过。横轴顺序另用`sequence`，数值来源另用`source_records`检查。

```json
{"rules":[{"kind":"inventory","sheet":"Data","row_range":[2,10],
 "key_columns":["A"],"value_columns":["B","C"],
 "expected":[{"key":["control"],"count":2},{"key":["future"],"count":2}],
 "source":"original PDF p5 Fig2 legend and two-point axis"}]}
```

## 填充短块虚线

`python tools/extract_dashes.py job.json`读取预先选定的一条虚线：支持小四边形/矩形填充块及可选的短描边，按中心线读取，在明确上限内做局部断口插值。示例见`examples/job_dashes.example.json`。参数长度均为PDF pt；小块长度、厚度、间隙及端点近邻阈值需按原图选择。`cap_width`仅在确认短描边确属该虚线时开启。

它不会自动分离多条同色虚线，也不读取位图。面板须排除图例、文字与其他同色碎片；离散片段之间缺口过大则保留缺值。近邻端点值会单独标记，不能称为直接读取目标位置。输出JSON保留片段、对象序号、逐点方法、来源摘要和校核叠图；零线附近读数不自动改0，近零裁定由原图复查留据。

## Agent看图接口

`tools/review_points.py`把已有脚本的候选读数接成“批量提议→少量局部看图→坐标修正→自动换算与回叠”的循环。支持源图坐标及放大检查图坐标回填、按摘要拦截过期批次、完整清单接续。默认只安排异常与部分稳定候选抽查；抽查通过不会把未看的点升级为已核验，也不会镜像补另一端或将缺值填0。详见[接口与调用示例](references/agent_review.md)。合成行为测试：`python tests/test_agent_review.py`。它不是新的自动图型检测器，实际看图仍消耗Agent额度。

## 验证范围

`verify`的`checked_value_types`分别统计契约内的数字、空白、文本和布尔值；检查格数不是有效数值数。`<0.001`等p值文本须显式加入契约，不能因它不是Excel数值类型而省略。`review_points`将不同角色位于同一像素附近的自动候选送回看图；它是检查提示，真正重合仍可保留共同读数。

本包附合成回归样例（数量及范围见 `tests/validation_summary.json`），运行后生成JSON结果，用于检验空检查、覆盖缺失、多面板标记、路径丢失、标准矩形点标记及证据输出等行为。合成测试不是论文独立复核。

早期本地A6图7回归曾得到233个数值，与旧摘录在0.6容差内一致；后续回看原图发现旧摘录另有描边柱漏取、缺柱误填0及面板身份错误。现已增加描边识别与独立内容审查契约。这也说明与旧摘录一致不能证明原文读取正确；本包不宣称整张图已完整核验。本仓库不上传论文PDF、项目底稿或逐点项目数据，详细案例记录保留在本地。原先“792个读数”的自述未在本轮复核。

## 相关工具与定位

[UnPlotter](https://github.com/ramcdona/unplotter)支持浏览器内矢量提取、标定和导出；[ChartDetective](https://github.com/m-damien/ChartDetective)支持交互提取与重建图检查。本包尝试把部分步骤组织成可留证的Agent流程；尚无依据声称其他工具都不具备这些能力或本包优于它们。

## 维护与许可

[GitHub仓库](https://github.com/regulushhw/pdf-figure-extract)为统一维护源；本地保留一份使用副本，更新时同步已核对的仓库版本。通用方法集中维护在技能与坑库，项目口径留在调用方既有文件。没有新验证经验就不追加，不为接续再造一套进度文件。

本项目的代码与配套文档采用 [MIT许可证](LICENSE)。第三方依赖仍遵循各自许可证；输入论文、项目数据及提取结果不因使用本工具而自动获得MIT授权。

