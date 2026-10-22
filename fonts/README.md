# fonts/

## 作用

挂载到 kkFileView 容器的 `/usr/share/fonts/truetype/custom`，
供 LibreOffice 转码 Office 文档时查找字体。

**字体是"格式不对"的头号原因**：文档里指定的字体找不到时，LibreOffice 会
按 fontconfig 默认规则挑一个风格完全不同的，字宽一变，换行、对齐、分页全错位。

## 本目录自带的内容

| 文件 | 作用 |
|---|---|
| `Carlito-*.ttf` | **Calibri 的度量兼容替代**（SIL OFL）。Word 默认西文字体就是 Calibri，缺了它英文/数字宽度会变 |
| `99-font-alias.conf` | 字体替换映射，挂载到 `/etc/fonts/conf.d/` |

### 映射规则（99-font-alias.conf）

| 文档里的字体 | 实际使用 | 说明 |
|---|---|---|
| Calibri / Calibri Light | Carlito | 度量完全兼容（SIL OFL） |
| Cambria | Caladea | 度量完全兼容（SIL OFL） |
| **DengXian / 等线 / 等线 Light** | **黑体 SimHei** | **不要映射到微软雅黑**，原因见下 |
| 宋体 | SimSun | 镜像自带 |
| 仿宋_GB2312 / 楷体_GB2312 | FangSong / KaiTi | 老文档常用写法 |
| Arial / Times New Roman | Liberation Sans / Serif | 度量兼容 |
| **Segoe UI / Verdana / Tahoma** | **DejaVu Sans** | 不修的话会落到中文黑体，西文全错位 |
| **Georgia / Garamond / Cambria Math** | **DejaVu Serif** | 衬线体不能落到黑体，公式尤其明显 |
| **Symbol** | DejaVu Sans | 不修会变方块 |
| **思源宋体 / Source Han Serif** | 宋体 | 宋体不能变黑体 |
| **思源黑体 / 方正兰亭黑 / 汉仪旗黑** | 黑体 | 同类无衬线 |

> **兜底原则**：西文字体找不到时，必须显式指到西文字体。
> 否则 fontconfig 会落到「文泉驿正黑」（中文黑体），
> 西文字形与字宽全变，整段错位——这比中文替换更隐蔽，因为中文字看起来没问题。

### 为什么「等线」不能映射到「微软雅黑」

实测（同一段 12 行中文，10.5pt，导出 PDF 后量行距）：

| 字体 | 平均行距 | 相对基准 |
|---|---:|---:|
| 宋体（基准） | 23.8 pt | — |
| 等线 → 黑体（现在） | **23.8 pt** | **完全一致** |
| 等线 → 微软雅黑（早期错误做法） | 25.9 pt | **+8.8%** |

微软雅黑的行高比宋体/黑体高约 30%（字体自带 metrics：雅黑 1.33em、宋体 1.01em）。
映射到雅黑后每行被撑高，整页行数变少、分页位置漂移，
用户观感就是"字体变大、行距变松"。黑体的行高与等线接近，是正确替代。

kkFileView 镜像**已自带**宋体、仿宋、楷体、黑体、微软雅黑，无需重复添加。

## 补充字体

需要更多字体时，把 `.ttf/.ttc/.otf` 放进本目录，重启 kkfileview 容器即可：

```bash
docker compose restart kkfileview
```

## 版权提醒

**不要放商业字体**（宋体、微软雅黑、等线等属于微软/方正的商业授权字体）。
kkFileView 镜像自带的那些由镜像提供方负责；**你自己往这里加的必须是可再分发的开源字体**。

可选开源字体：思源黑体/思源宋体（SIL OFL）、Noto CJK（SIL OFL）、
文泉驿系列（GPL + 字体例外）、Carlito/Caladea（SIL OFL，兼容 Calibri/Cambria）。

## 治本建议

LibreOffice 与 Word 是两个不同的排版引擎，**文本框、图文环绕、表格边框、
分页位置**这些差异无法靠补字体消除。如果某类文档必须与原件完全一致，
正确做法是在源端另存为 PDF 再上传预览。
