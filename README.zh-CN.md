# Cuddler for ComfyUI

> **开发中。** 插件还没有上架 Comfy Registry。1.0 版会加入 Cuddler Video、Image、Speech、Music
> 四个节点；在那之前只有设置页。

[English](README.md)

用你的 [Cuddler Platform](https://platform.cuddler.ai) API key，在 ComfyUI 工作流里生成视频、图片、语音和音乐。
Cuddler 公开模型目录里的全部模型都可以通过这四个节点使用。运行前节点上会显示价格，每次运行都有花费上限，由 Cuddler 服务端强制执行。

## 需要准备

- ComfyUI 0.14.0 或更新的版本。
- 一个 Cuddler Platform API key。在 <https://platform.cuddler.ai/console/keys> 创建，在
  <https://platform.cuddler.ai/console/billing> 充值。

## 安装

1.0 发布后：在 ComfyUI-Manager 里搜索 **Cuddler**，或者运行 `comfy node install cuddler`，然后重启 ComfyUI。
插件不需要额外安装 Python 包。

## 填写 API key

打开 **设置 → Cuddler**，粘贴 key，点 **保存**。页面上会显示 key 的最后 4 位和可用余额。

部署在服务器上时，改用环境变量 `CUDDLER_API_KEY`。它的优先级高于设置页里保存的 key。

设置页和价格标签只响应从 `localhost`、IP 地址或局域网名称（例如 `my-pc.local`）打开的页面，这样其他网站就改不了你的 key。如果通过域名或隧道访问 ComfyUI，请把该域名加入环境变量 `CUDDLER_ALLOWED_HOSTS`（多个用逗号分隔，`.example.com` 表示它下面的所有域名），然后重启 ComfyUI。

## 费用

- 运行前，每个节点都会显示价格。价格来自 Cuddler 的估价接口，查价不收费。
- 每个节点都有单次花费上限（默认 $2）。可能花费超过上限的任务，Cuddler 不会创建，实际扣费也不会超过上限。
- 用同样的参数和同样的 seed 再次运行时，7 天内会直接取回上次的结果，不会重复扣费。想要新的结果，改一下 seed 即可。
- 视频、语音、音乐任务在排队时停止运行，任务会被取消，不扣费。图片任务和已经开始生成的任务无法取消，会正常完成并扣费，再次运行这个节点即可取回结果。

## 隐私与安全

- key 保存在 `ComfyUI/user/__cuddler/credentials.json`。ComfyUI 不会通过网页接口提供 `__` 开头文件夹里的文件；在 macOS 和 Linux 上，这个文件只有你的账户能读。
- key 不会写进工作流、图片或视频的元数据、ComfyUI 的设置或日志。
- `ComfyUI/user/__cuddler/state.sqlite3` 保存运行指纹和文件的哈希值、幂等键、任务和上传 ID 以及每次运行的花费上限，这样即使崩溃或断网，也不会重复创建任务或重复上传文件。这里不保存提示词和素材。ComfyUI 启动时，插件会删除最后一次更新已超过 8 天的任务记录和超过 29 天的上传记录（没有传完的上传超过 1 小时即删除）。
- 发给 Cuddler 的每个任务都带有元数据 `client: comfyui-cuddler/<版本号>`，用来统计从 ComfyUI 发起的任务数量；每个请求的 User-Agent 里写有插件和 Python 的版本号。除此之外，不会发送任何关于你或你电脑的信息。上传的参考素材会带上它的文件名。
- ComfyUI 本身没有登录功能。能打开你 ComfyUI 页面的人都能用你的 key 花钱，所以不要把 ComfyUI 开放到不可信的网络上。单次花费上限和账户的每日上限会限制最多能花多少。
- 如果 ComfyUI 运行在另一台机器上，在设置页填写的 key 会经过网络传过去。这种情况建议改用 `CUDDLER_API_KEY`。

## 开发

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -t .
npm test
```

发版前运行 `python scripts/export_catalog_snapshot.py`，更新插件自带的模型目录。

## 许可证

MIT，见 [LICENSE](LICENSE)。
