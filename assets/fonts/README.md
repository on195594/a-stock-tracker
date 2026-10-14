# 随应用提供的中文字体

- 原文件：Noto Sans SC Regular，SIL Open Font License 1.1，见 `OFL.txt`；版权声明保留于字体元数据，许可未设保留字体名。
- 来源：https://github.com/notofonts/noto-cjk/blob/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf
- 许可来源：https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE
- 获取日期：2026-09-24；原 OTF 8331336 bytes，SHA-256 `faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9`（见提交 `ad9a3bd`）。
- 2026-10-13 按用户确认的首屏性能取舍，用 fontTools 4.66.1 裁剪为 `NotoSansSC-Subset.woff2`：去掉 CJK 扩展 A（U+3400–4DBF）与补充平面汉字，保留其余 24088 个码位（含完整 CJK 基本区 U+4E00–9FFF、标点、拉丁与假名）及全部排版特性；4673484 bytes，SHA-256 `7ff2fb79dcac8b7491aee6aedddbc8f67821fa32084981018f2a5be90401961f`。

  ```python
  # uv run --with fonttools --with brotli python
  from fontTools import subset
  from fontTools.ttLib import TTFont

  src = "NotoSansSC-Regular.otf"
  keep = [cp for cp in TTFont(src).getBestCmap() if not (0x3400 <= cp <= 0x4DBF or cp >= 0x20000)]
  opts = subset.Options()
  opts.layout_features = ["*"]
  opts.name_IDs = ["*"]
  opts.name_languages = ["*"]
  opts.notdef_outline = True
  f = TTFont(src)
  s = subset.Subsetter(opts)
  s.populate(unicodes=keep)
  s.subset(f)
  f.flavor = "woff2"
  f.save("NotoSansSC-Subset.woff2")
  ```

已知代价：扩展区生僻字（极少数公司名或资料原文可能出现）会显示为缺字方框；生产关闭 CDN 后不会从外部补字。字体响应带 30 天缓存，内容变化时必须改文件名，否则已访问用户会继续使用旧字体。

Flet 页面通过 Flutter 启动 FontManifest 注册同源字体，先完成字体加载再渲染中文；不再依赖 page.fonts 的异步运行时注册，以免首帧提前请求未打包的 Noto 回退分片。同时关闭页面主题切换动画：Flet 首次远程更新从默认 Roboto 切换到中文主题，Flutter 默认 200ms 插值前半程仍使用旧字体，会在中文已加载时仍触发回退请求。启动 manifest 合并当前 Flet 自带的图标、数学字体与 Roboto，中文条目复用预加载的同一 URL，不新增字体文件或外部 CDN。已知裁剪缺字范围仍如上，不用回退路径返回不匹配的字体来掩盖缺字。

同源字体避免中文标签、公司名与解释/反证依赖运行时 CDN。此目录只允许公开静态资源，不放研究批次或私人数据。裁剪命令是一次性操作，不增加项目字体构建工具或运行时依赖。首次下载成本仍需真机冷启动测量，现有浏览器回归不证明性能预算达标。
