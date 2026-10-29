# Nuclear Thunder 互动地图

[English](README.md) | 简体中文

面向《战争雷霆》"核升级"事件的社区自建互动参考地图。

## 使用地图

- 先选择 **热带海岸** 或 **东南渔湾**。热带海岸带地形高程覆盖层；东南渔湾有独立提取的
  载具道路网络与导航网格，可用于路线预览。
- 选择场景。选择器会按《战争雷霆》的平衡等级尺度（`BR = 1 + rank / 3`）把任务的
  等级区间换算为 BR，并把下界抬到该事件 8.7 的最低 BR（这条匹配规则并不存放在
  客户端文件里）。如果生成的数据里给出了明确的 BR 值，则以该值为准。
- 用 **图标** 选择器在 NATO（默认）、WT 和苏联三种兵种符号之间切换整张地图的
  图标风格。
- 任一张地图上的 **交换阵营** 会载入由游戏定义的镜像任务，包括对立阵营的点位
  位置和任务路线。
- 展开地图控件中的各个文件夹，可显示部队、机场、仓库、路线和射程覆盖层。
- 用地图工具里的 **威胁覆盖** 可以着色显示某一方的雷达（黄色）与防空武器（红色）
  在指定离地/海面高度上能"看到"哪些空域，其中包含地形遮蔽。它使用与射程环相同的
  提取射程，不模拟 SAM 的高度上限、雷达杂波和地球曲率。
- 用 **语言** 在 English 与简体中文之间切换界面。首次访问会跟随浏览器偏好，之后
  会记住你的选择。
- 所选地图、场景、阵营、图标风格、语言、抽屉展开状态和威胁设置都会保存在浏览器
  中，下次访问继续沿用。
- 点击任一单位可查看其传感器、武器、装填、弹药和重生信息。图例中还会列出点位编成、
  车队编成以及任务级重生规则。

## 数据更新

地图会检查公开的 [War Thunder Datamine](https://github.com/gszabi99/War-Thunder-Datamine)
中与"核升级"相关的变化。更新会先准备好供审阅，通过后才进入公开地图。

更新器除了任务本身，还会跟随任务导入的舰船配置集文件。这样可以把 1970 年代舰队与
1980/2018 年舰队区分开，并让每艘舰船落在任务定义的巡逻路线上。

地形与道路与事件数据分开维护，因为它们来自编译后的游戏关卡。

### 提取地形高程图

两张地图都从已安装游戏客户端的编译关卡（`<War Thunder>\levels\*.bin`）读取高程。
每个提取器都会输出一张带晕渲的地形覆盖层、一张 16 位高程栅格（`*_16bit.webp`：
红 = 高字节，绿 = 低字节，步进 0.25 m）以及地图加载时读取的元数据 JSON，因此重新
导出无需改动 `index.html`。高程单位为绝对米；两张地图的海面均为 0 m。

热带海岸使用的是 `HM2` 高程图。解码需要 Oodle，由 `pyooz` 包提供
（`pip install pyooz`；也可以用 `--ooz <ooz.exe>`）：

```text
python tools/extract_heightmap.py "F:\Steam\steamapps\common\War Thunder\levels\air_archipelago.bin" --output-dir .
```

当前东南渔湾关卡同样包含 `HM2`。用同一个提取器，但指定东南渔湾的文件名前缀，以免覆盖
热带海岸资源：

```text
python tools/extract_heightmap.py "<War Thunder>\levels\air_south_eastern_city.bin" --output-dir generated\southeastern_city_heightmap --output-stem southeastern_city --height-downsample 2
```

如果没有 `pyooz`，可加 `--asset-explorer <folder>` 使用 Windows 上本地的 Dagor Asset
Explorer 解压 DLL，或使用 `--ooz <ooz.exe>`。东南渔湾的源数据是 4096 x 4096 网格、间距
32 m；按 2 x 2 源单元取平均后，浏览器拿到的是 2048 x 2048、间距 64 m 的栅格。两张
地图都保留 0.25 m 的垂直精度和双线性高程采样。

对于没有 `HM2` 的旧版东南渔湾关卡，仍可用 `tools/extract_lmap_heightmap.py` 作为地形
网格回退方案（`--resolution 2048` 可得到更大的栅格）。

从同一个已安装关卡刷新东南渔湾的道路与导航：

```text
python tools/extract_roads.py "<War Thunder>\levels\air_south_eastern_city.bin" --include-local-roads --output road_network_city.json
python tools/extract_navmesh.py "<War Thunder>\levels\air_south_eastern_city.bin" --output navmesh_city.json
```

导航提取需要 `zstandard`（`pip install zstandard`）。路线预览是根据导出的道路几何和
导航网格重建的，并非录制游戏内实时 AI 路径。战术底图另行维护，不会被这些命令替换。

要在本地测试，请在此文件夹中启动一个小型本地 Web 服务器，然后在浏览器中打开
`http://localhost:8000`（例如运行 `python -m http.server 8000`）。直接用磁盘打开
`index.html` 在多数浏览器中会被数据文件的加载策略拦住。

更新器也可以手动运行：`python tools/update_from_datamine.py`。它会下载锁定版本的
"核升级"输入，重新生成全部四个地图变体以及单位/射程数据，并把来源提交记录在
`datamine-lock.json` 中。提交或合并更新提案前，请先审阅生成的文件和地图预览。

如果要用本地完整 checkout 中的单位定义，可加
`--aces-root F:\WT_Stuff\War-Thunder-Datamine\aces.vromfs.bin_u`；除非你修改了清单
（manifest），任务输入仍来自所配置的 datamine 版本。

### 同步本地载具美术资源

如果你有 datamine 的完整本地克隆，可以用下面的命令把匹配的槽位图标、载具卡片美术、
兵种 SVG 和战略轰炸机出生点符号复制进本仓库：

```text
python tools/sync_datamine_assets.py --datamine F:\WT_Stuff\War-Thunder-Datamine
```

该命令会保留已有的本地美术资源。只有确实想覆盖时才加 `--force`。如果它复制了新的
兵种 SVG，请重建地图实际加载的图标集：

```text
python tools/build_icon_templates.py
```

逐单位的完整结果会写入 `generated/datamine_asset_sync_report.json`；命令也会打印仍然
缺少美术资源的单位。把这些文件保留在本地，可以让部署后的地图不依赖在线的 Wiki 或
datamine 链接。

## 翻译

界面文案位于 `i18n/<locale>.json`，其中 `en.json` 是回退语言，因此缺失或只翻译了
一半的语言包不会让界面空白。要新增语言，请复制 `i18n/en.json`、翻译其中的值，并把
其 id 加入 `index.html` 的 `I18N_SUPPORTED_LOCALES`；语言选择器就是按这个列表构建的。

每个语言包通过自己的 `language.nativeName` 声明自身名称（例如 `"English"` 或
`"简体中文"`），这样选择器能以该语言的原生写法显示语言，且没有任何语言包需要携带
其他语言的译名。该值请用该语言自己的文字书写；缺失时才会退回 locale id。

各语言包的键名和 `{placeholder}` 名称必须与英文包完全一致，页面中每一处 `t("key")`
或 `data-i18n` 键都必须存在。`tools/check_frontend.js` 会同时校验这两点，因此过期的
翻译会在到达地图之前就让 CI 失败。

来自数据的名称不会在语言包中重复：

- 单位名称从 `unit_display_names.json` 加载，结构为
  `{"<locale>": {"<id>": "<name>"}}`，由 `units.csv` 中客户端提供的每个语言列导出；
  扁平的 `{id: name}` 表仍可作为英文默认值使用；
- 武器名称从 `unit_weapon_display_names.json` 加载，由 `units_weaponry.csv` 导出；
  没有对应行的武器会退回清洗后的标识符，因此缺少翻译也不会留下空标签；
- 场景标签在 `presets.json` 中保留英文默认值，语言包可以用 `preset.<id>` 键覆盖；
- 地图标签（`map.<id>`）、阵营名称和内部单位角色（`role.<role>`）由语言包翻译，
  回退值为数据里的原值或清洗后的标识符。

`unit_display_names.json` 中缺失的单位仍会通过标识符清洗来显示，因此 datamine 里新增
的条目在其名称被翻译之前就能出现在地图上。

## 载具卡片美术

载具卡片美术根据 datamine 中单位的 `model` 字段自动推导。缺失的本地美术请按
`images/portraits/<model>.png` 添加；例如 MPQ-34 CWAR 的 model 是
`mim_23_hawk_radar_cwar`，其文件就是 `images/portraits/mim_23_hawk_radar_cwar.png`。
新增单位不需要改动 `index.html`。

地图上的兵种符号分别存放在 `images/icons/vanilla`、`images/icons/nato` 和
`images/icons/pact`。它们是 datamine 中的透明 SVG；渲染时由地图施加阵营颜色。

### 游戏内显示名称

可读的单位名称来自 datamine 本地化表 `lang.vromfs.bin_u/lang/units.csv`（`_0` 行是
全名）。该表每种语言一列，提取器会一次性写出地图提供的语言（英文和简体中文）。改动 `unit_specs.json` 或拉取新的 datamine 之后，请刷新查找表：

```text
python tools/extract_display_names.py --units-csv F:\WT_Stuff\War-Thunder-Datamine\lang.vromfs.bin_u\lang\units.csv
```

该命令写出 `unit_display_names.json`，结构为 `{"<locale>": {"<id>": "<name>"}}`。地图
优先使用当前语言，其次英文，只有当仅限该事件的单位没有本地化行时才退回内部标识符。
值中的零宽空格（中文列用它作分词符）会被清理掉。

`_1` 行仅在短名与全名不同时，才会额外导出为 `<id>#short` 键。点位弹窗、机场防御列表
和图例摘要使用该短名，使过长的载具名不会撑破标记按钮，而背后的单位卡片仍保留完整的
`_0` 全名。

武器标签来自 `lang.vromfs.bin_u/lang/units_weaponry.csv`，其中每个武器文件名都有一行
`weapons/<name>`，有时还有一行 `/short`。这些键保留客户端的原始大小写，因此匹配时
不区分大小写；没有全名时使用短名，而完全没有对应行的武器保留清洗后的标识符，而不是
空标签：

```text
python tools/extract_weapon_names.py --weaponry-csv F:\WT_Stuff\War-Thunder-Datamine\lang.vromfs.bin_u\lang\units_weaponry.csv
```

该命令写出 `unit_weapon_display_names.json`，采用同样按 locale 分键的结构。

AI 交战环来自每件武器的 `aimMaxDist` / `attackMaxDist` 以及载具的 `AttackMaxRadius`：
AI 只在三者中最小的那个范围内开火，而它往往远小于导弹的实际射程（I-HAWK 能飞
110 km，但瞄准距离只到 40 km）。一件载具挂多件武器时，取其射程最远的那件。

提取出的单位规格还保留 datamine 的 `type`、`onRadarAs` 和 `expClass` 字段。渲染器会
优先使用这些 HUD 分类，再退回到基于任务角色/名称的启发式判断。

本项目是非官方社区项目，与 Gaijin Entertainment 无关。
