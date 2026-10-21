# 文字识别与标定辅助

用途：扫描图/嵌入图片的刻度、图例、单位带原图坐标输出，Agent一次检查并修正后转配置。先复用PDF文字层与原表；只有需要识别图片文字时运行本模块。不识别数据曲线，不自动绑定情景与线型，不调用模型或云服务，不自动安装引擎。

## 接入任务生成器

```text
python tools/prepare_task.py configure 过程目录 --part 候选ID --method bitmap_points
python tools/prepare_task.py ocr 过程目录 --part 候选ID --image 原图.png --bounds 左 上 右 下 --language en-US
```

有且仅有一个原图资产时可省image；已配置image/bounds可复用。多图页面应明确原图与面板框，不能默认取第一张。纯扫描PDF沿用生成器导出的原图资产，不依赖文字层；整页扫描也可用，先按面板给bounds，避免混合多个坐标系。bounds只是Agent选定的面板范围，不代表程序已识别正确。源像素坐标不能与PDF pt或预览缩放坐标混用。

Windows默认复用系统OCR（en-US、zh-Hans-CN等需本机已装语言）；其他系统默认Tesseract，需要可执行文件在PATH或可信环境变量FIGURE_TESSERACT，语言使用eng/chi_sim等引擎代码。不可用时返回错误，不安装、不偷偷换云服务。Windows桥接仅在子进程设置执行策略，不修改系统/用户配置。

单独处理图片、指定旋转/放大或使用Tesseract：

```text
python tools/ocr_assist.py recognize 原图.png 过程目录 --bounds 左 上 右 下 --engine windows --language en-US
python tools/ocr_assist.py recognize 原图.png 过程目录 --engine tesseract --language eng --rotations 0 90 --scale 2
```

## 一份候选，一次确认

输出analysis.json、labels.tsv及labels_check.png：每个字词/文本行有稳定ID、原文、原图边界框、旋转来源和引擎置信度。Windows没有提供逐词置信度，记录null；Tesseract置信度按引擎原值保留，不能当准确率。默认放大2倍、识别0/90度两种方向，再换回原图坐标。90度识别优先用于竖排轴题；旋转产生的横排碎字不塞进图例候选。

按面板外侧排列整理横/纵轴数字组，拟合线性关系、提示冲突或非线性；图例/轴题/单位只给文字候选，不靠正则决定研究身份。数字位置默认是文本中心，**不是已检测到的刻度线位置**。同轴只有一组能通过线性检查时，confirmation_template.json预填对应word_id，viewed仍为false。多组不替Agent选，缺失、符号错误或不支持内容保留。

复制模板为确认文件，保留摘要，实际看图后填写：

```json
{
  "analysis_fingerprint":"沿用模板",
  "source_sha256":"沿用模板",
  "review_image_sha256":"沿用模板",
  "viewed":true,
  "note":"实际核对的面板、轴刻度与限制",
  "x":[{"word_id":"r0_l6_w0"},{"word_id":"另一个数字ID"}],
  "y":[{"word_id":"纵轴数字ID"},{"word_id":"另一个数字ID"}],
  "unit":{"line_id":"单位所在行ID","text":"Ml/d","reason":"原图斜杠被OCR误识别，已回图纠正"},
  "series":[{"line_id":"图例行ID","name":"Observed"}]
}
```

数字项省value使用识别值，省position使用文本中心；可显式给value或position，修正须写reason。单位与识别行不一致也须写reason。未知数字不将O自动换0，不猜小数点、负号或千位分隔。正文支持的单位与轴题冲突另留说明，不以纠错名义隐藏冲突。

```text
python tools/prepare_task.py ocr-apply 过程目录 --part 候选ID --decision 确认.json
```

应用前备份决定文件，核对当前原图/检查图摘要、线性残差和当前part的图像；写入紧凑bitmap_points模板的x_ticks、y_ticks、unit及图例文字出处。**不自动填取样范围、scope.series或颜色/线型选择器**：图例文字正确不证明曲线身份正确。其他既有方法可先独立导出确认配置，再显式使用：

```text
python tools/ocr_assist.py confirm analysis.json 确认.json 确认配置.json
```

原图片、检查证据、确认文件或应用后的标定发生变化会阻止沿用旧确认。输出保留识别原文、纠正理由及图片位置，不修改原PDF和研究工作簿。看图记录属于同一Agent自检，不是独立核验。

## 边界与验证

- 当前标定只支持线性轴；重复/不足刻度、残差冲突须处理，不通过放宽容差自动过关。两点拟合通过不证明轴线性。
- Windows返回非零TextAngle时，边界框属于倾斜校正坐标；模块保留告警并阻止这些坐标直接标定，未实现任意角度纠偏。
- 小字、粘连、指数/上下标、复杂单位、整页多面板仍可能漏读；正则分组不等于语义识别。已确认的数字和未知标签分开记录。
- 真实测试使用本机Windows英语引擎及A2嵌入位图；Tesseract TSV接口经过合成解析测试，未声称本机安装并运行Tesseract，也未证明中文/所有扫描件准确率。

运行`FIGURE_OCR_INTEGRATION=1`环境下的`python -X utf8 -B tests/test_ocr_assist.py`可包含真实Windows识别测试；未设置时该项跳过。测试只用合成图片/PDF。复核生成器用tests/test_prepare_task.py。

实现依据：[Windows OCR结果结构](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrresult?view=winrt-26100)、[BoundingRect与TextAngle坐标约定](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrword.boundingrect?view=winrt-26100)、[Tesseract官方TSV格式](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)。
