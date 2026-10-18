# pdf-figure-extract

**状态：实验性Skill，持续改进中。** 支持范围和验证边界见下文；不保证任意论文都可自动提取。

面向Agent的论文图表数值化工具：提取数值，保存标定与出处，生成校核叠加图，并检查Excel写入是否一致。执行入口见 [SKILL.md](SKILL.md)。

## 当前支持与限制

- 盘点PDF候选对象；读取部分矢量柱图、直线段曲线和对称点标记。逐面板确认底本，不能自动识别所有图型。
- 自动标定目前限等距线性刻度；对数轴、Bezier路径、堆叠/浮动柱和复杂位图需另用方法。
- 输出保留原数据字段，并增加统一`_evidence`，包括PDF摘要、页面、方法、标定和逐点坐标。原文图号与研究条件由job明确提供，工具不能自行理解论文条件。
- `build_workbook.py`是版式辅助函数；它不会自动生成全部说明、依据或完成视觉检查。
- `verify.py`核对声明数值区与JSON是否一致，不证明原图读得正确。检查一次完整覆盖，另做原图检查和保存预览，不用重复运行冒充独立核验。

## 运行

安装依赖见 `requirements.txt`。在调用方指定的工作目录运行：

```sh
python /path/to/tools/scan_pages.py paper.pdf --json scan.json
python /path/to/tools/extract_bars.py job.json
python /path/to/tools/verify.py workbook.xlsx checks.json
python /path/to/tests/test_regressions.py
```

柱图示例见 `examples/job_bars.example.json`；实际槽位边界、图例映射、单位和时段须逐图确认。输出前缀指向工作区，原始PDF只读；已有文件修改前备份。

## 验证范围

本次修正附10项合成回归样例，运行后生成JSON结果，用于检验空检查、覆盖缺失、多面板标记、路径丢失、标准矩形点标记及证据输出等行为。合成测试不是论文独立复核。

本地A6图7重跑：288个预期位置中提取233个数值、保留55个缺值；233个数值与项目既有摘录在0.6容差内一致，最大绝对差约0.266。此项属于与既有摘录的一致性回归，未独立证明旧摘录准确，也未完成55个缺值的逐项裁定；不能称整张图已完整核验。本仓库不上传论文PDF、项目底稿或逐点项目数据，详细案例记录保留在本地。原先“792个读数”的自述未在本轮复核。

## 相关工具与定位

[UnPlotter](https://github.com/ramcdona/unplotter)支持浏览器内矢量提取、标定和导出；[ChartDetective](https://github.com/m-damien/ChartDetective)支持交互提取与重建图检查。本包尝试把部分步骤组织成可留证的Agent流程；尚无依据声称其他工具都不具备这些能力或本包优于它们。

## 维护与许可

[GitHub仓库](https://github.com/regulushhw/pdf-figure-extract)为统一维护源；本地保留一份使用副本，更新时同步已核对的仓库版本。通用方法集中维护在技能与坑库，项目口径留在调用方既有文件。没有新验证经验就不追加，不为接续再造一套进度文件。

本项目的代码与配套文档采用 [MIT许可证](LICENSE)。第三方依赖仍遵循各自许可证；输入论文、项目数据及提取结果不因使用本工具而自动获得MIT授权。
