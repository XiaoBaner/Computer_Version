# 普绪喀角色素材与场景

## 角色

母图采用深棕短发、灰绿色开衫和米白内搭，表情围绕平静倾听设计。[角色母图](../assets/avatar/psyche-v1/concept.png) · [七种表情对照板](../assets/avatar/psyche-v1/expression-sheet.png)

- 角色图层和七张完整表情参考图使用 **1086 × 1448** 的透明画布。坐标以左上角为原点，左右按观众视角命名。[角色素材清单](../assets/avatar/manifest.json)
- 基础叠层依次为身体、头部、两边眉毛和瞳孔，再叠放眼睑和嘴型。半闭和闭眼素材各自包含遮挡关系。表情可覆盖睁眼和闭口的默认素材，手动下拉仍可选择其他眼睑或嘴型。
- 自然保持平静直视，专注抬眉，克制关切抬内眉，思考垂眼侧看，澄清抬单侧眉，轻微舒展用闭口微笑，收住用抿唇。

## 场景

室内与窗景拆成同尺寸图层。默认、安静和练习共用同一构图，分别叠加暖白、偏冷和浅暖光。三条环境声为独立的 12 秒程序合成 WAV，打开环境声后随场景交叉淡化。[场景素材清单](../assets/scene/manifest.json)

## 本地预览

双击 [start-avatar.cmd](../start-avatar.cmd)。选择手动预览后可自由组合表情、眼睑和嘴型，勾选环境声可试听场景。在浏览器里关闭显示锚点可看纯画面，勾选显示表情参考图可比对对应的完整参考。

离线样例使用静音语音夹具和模拟播放回执。实机接入后，用真实播放器确认至少 2 次正常开口、3 次打断、1 次对话触发的场景和环境声过渡，并测量停声到口型归位的时差。[验收标准](https://github.com/XiaoBaner/Computer_Version/blob/0b59aa050885861c0ad3947baae6b32274569f0f/docs/demo-development-baseline.md#8-demo-验收标准)

## 生成提示词

角色和房间使用 Codex 内置 ImageGen。以下是母图、场景和局部变化的提示内容。

**母图**

> Original isolated 2D conversational avatar named Psyche. Invent a young adult East Asian woman with a softly rounded oval face, natural dark brown eyes, a deep espresso chin-to-shoulder bob with a soft center part, a muted sage-green cardigan over an ivory shirt, no jewelry. Semi-realistic character illustration, gentle daylight, frontal eye-level gaze and gently closed neutral mouth. Show the complete hair and shoulders through waist with generous alpha margin. Single transparent sprite, no stage, shadow, text or watermark.

**房间**

> A contemporary listening room, semi-realistic editorial illustration. Wide eye-level view, rounded window on the far left with pale mountain silhouettes and gray-green trees, low oak shelf and ceramic vase at the far right, central wall intentionally calm light warm plaster. Sage and oatmeal palette, diffuse light, softly realistic material texture. Fill edge to edge, no people, text or multiple scenes.

**局部变化**

使用母图作为每次编辑的对齐参考，保留画布、比例、机位、光照、皮肤质感、发型、服装和透明通道。每次只改变对应的局部。

| 素材 | 提示内容 |
| --- | --- |
| 专注 | Quietly alert. Raise both brows and the upper lids just a little while looking straight ahead. |
| 克制关切 | Raise the inner third of both eyebrows with a relaxed gaze and no smile. |
| 思考 | Briefly glance slightly downward and to the viewer’s left, with the same frontal pose. |
| 澄清 | Raise only the eyebrow on the viewer’s right a little. |
| 轻微舒展 | A small closed-mouth smile of relief, corners raised slightly, same frontal gaze. |
| 半闭与闭眼 | Lower both upper lids 55% over the irises for half-close. For closed, use gentle natural lash lines with lids fully closed. |
| 微开、宽口、圆口、抿唇 | A narrow dark opening for quiet speech. A natural open ah. A small rounded oh. Gently compressed closed lips for stopping speech. |
| 头部叠层底图 | Paint over eyebrows, eyelashes, iris openings and lip color with matching skin for independent layers. Preserve the nose, outline, hair and wardrobe. |
