# PDF 图表数值提取

把论文中的图表读成可整理的数据：程序负责准备图像、定位候选、坐标换算和纠错；Agent 或研究者确认图表、坐标、系列身份并检查结果。

## 能做什么

- 提取部分矢量柱图、折线、虚线和点标记，也可从位图中提出柱、曲线和误差线候选。
- 显示原图和带编号的叠图，移动、补充或删除点；按既定坐标轴换算，保留来源。
- 导出过程数据；可选地将核对后的数据写入现有 Excel，保留备份和原始对象。
- 接收 WebPlotDigitizer、UnPlotter、ChartDetective 的 CSV 导出。需要用户在这些工具中自行导出；本工具不会自动操作它们。

工具不会仅凭 PDF 自动理解图例、单位和系列身份。低分辨率、遮挡、交叉线以及不支持的图形可能需要人工处理；无法辨认时保留缺值。图读数据是约数，不能当成作者提供的原始数据。

## 开始使用

需要 Python 和 [requirements.txt](requirements.txt) 中的依赖：

```sh
python -m pip install -r requirements.txt
python tools/prepare_task.py paper.pdf
```

第一条命令安装依赖；第二条准备页面图像、候选图表清单和配置，**不会直接生成完整数据表**。结果默认写入当前目录的 `figure_tasks/`；已有项目另有指定目录时，可用 `--out`。

1. 先查看论文及补充材料，确定需要哪些图表；有同条件原表时优先摘录原表。
2. 在准备结果中查看候选图和图注，确认图例、单位、坐标轴、系列及取样范围；用 `configure` 选择适用的方法并填写配置。
3. 用 `trial` 试读，查看原图与叠图。需要纠错时，移动、补充或删除候选点；图线身份和遮挡仍由人判断。
4. 整理已确认的数据和出处，按需要保存到 Excel；保留原图、读图约数和未能辨认的位置。

具体命令与配置见 [从 PDF 准备任务](references/task_preparation.md) 和 [提取与纠错流程](references/fixed_tasks.md)。批量保存现有 Excel 还需要 Node.js、已配置的表格引擎和 PowerShell；普通 Python 环境仍可使用独立提取工具，详见[工作簿保存](references/tool_reference.md#共享工作簿保存)。

## 按需查看

- [图表提取 Skill](SKILL.md)：Agent 的工作流程和证据要求。
- [文字识别与坐标标定](references/ocr_calibration.md)：扫描图的文字候选及线性刻度确认。
- [复杂柱图与曲线](references/complex_geometry.md)：误差线、重叠和交叉曲线的处理。
- [工具参考](references/tool_reference.md)：模块参数、示例、第三方导入与保存。
- [常见问题](pitfalls.md)：常见读图错误和处理方式。

## 验证与许可

`tests/` 包含合成数据与接口测试；通过测试只说明测试覆盖的行为正常，不代表任意论文图表的识别准确率。图例、标定和最终读数仍须对照原文。

代码和文档采用 [MIT 许可证](LICENSE)。输入论文、第三方依赖及提取数据仍遵循各自的许可。
