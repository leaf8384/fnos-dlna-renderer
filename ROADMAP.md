# 未来计划

> 记录于 2026-10-22。当前版本 **1.0.1**（1.0.0 已稳定运行：单曲/连切/暂停后切/EOS 重播/音量记忆 全部实测通过；1.0.1 修中文设备名），**暂无必须做的事**，以下按优先级排列，做到哪算哪。
> 应用化设计的完整方案见 [`docs/app-console-plan.md`](app-console-plan.md)。

## P0 · 待验证（决定后续路线，30 分钟）

- [x] ~~**中文广播名能不能不源码就支持**~~ —— **已实测：能，不用改 C（2026-10-22）**
      `main.c` 用 GLib 的 `G_OPTION_ARG_STRING` 收参数，GLib 按 locale 转 argv，猜对了。
      根因是**进程里根本没有 `LANG`**：飞牛只注入 `TRIM_SYS_LANGUAGE=zh-CN`（glibc 不认这个变量），
      appcenter 拉起的环境里 `LANG/LC_*` 全无（`/proc/PID/environ` 已确认），于是落到 C locale。

      ```
      LANG=C.UTF-8    -f "客厅音箱" → <friendlyName>客厅音箱</friendlyName>   ✔
      LANG=en_US.UTF-8 -f "客厅音箱" → <friendlyName>客厅音箱</friendlyName>   ✔
      LANG=C          -f "客厅音箱" → Failed to initialize: Invalid byte sequence in conversion input  ✘
      ```

      **修法：`cmd/main` 启动前 `export LANG=C.UTF-8 LC_ALL=C.UTF-8`**（systemd unit 同理加 `Environment=`）。
      已应用并在机器上实测：中文名启动成功、`description.xml` 输出正确。
      **结论：应用化里"改广播名"功能就是改配置 + 重启，工作量不翻倍。**
      坑：`--dump-devicedesc` 不是纯本地操作，它仍要初始化 UPnP 抢 49494 端口，测试必须带 `-p <空闲端口>`。

- [ ] **给 `description.xml` 补 `dlna:X_DLNADOC`**（改一行 + 重编）
      对照 MiCast-Go 的实现，它写了 `<dlna:X_DLNADOC>DMR-1.50</dlna:X_DLNADOC>`，我们上游没有（已 grep 确认）。这是"某些国产 App 搜不到设备"的可疑原因，补上就能验证。
      改的是 `src/device-desc.xml`，configure 时被打进二进制，必须重新编译，不能运行时替换。

## P1 · 单页控制台（唯一要做的一块）

一个页面搞定「看 + 改 + 生效」，不再分只读/可写两期。

- [ ] `dlna-console`（Go 单文件 + `//go:embed` 单页），监听 `:8790`
- [ ] `manifest` / `fnpack.json` 填 `service_port = 8790`，应用中心出现「打开」
- [ ] 页面（同一个界面里全部搞定）：
      - 运行状态 + 版本
      - **广播名**（文本框）
      - **声卡**（下拉，`aplay -l` 枚举成 `ALC887-VD Analog` / `HDMI 0` 这种可读形式）
      - **网卡**（下拉，`ip -br link` 过滤虚拟网卡，标出哪张有默认路由）
      - 「播放测试音」按钮（`aplay` 放 2 秒正弦波，不依赖 GStreamer）
      - 「保存并重启」按钮
- [ ] 保存流程：校验 → 原子写 `renderer.conf` → `cmd/main restart-renderer` → 嗅探 1900 的 NOTIFY 确认新名字已广播 → 失败回传 `renderer.log` 末 20 行
- [ ] `cmd/main` 增加 `restart-renderer` 子命令（**只**重启渲染器，不动控制台），启动参数只在这一处维护，避免控制台里另写一份导致漂移
- [ ] `cmd/main start` 管两个 pidfile（渲染器 + 控制台一起起落）
- [ ] `UDN` 独立存储，改名不产生幽灵设备
- [ ] 声卡占用检测（`fuser /dev/snd/pcmC*D*p`），占用时提示而非静默失败

### 不做：控制台内的开关

开关完全交给应用中心的**启用 / 停用**：

```
停用 → cmd/main stop  → 渲染器 + 控制台一起停
启用 → cmd/main start → 一起起
```

代价要说清楚：**应用停用后连配置页都进不去**，要改配置得先启用。接受这个代价，换来少一个状态要维护（不用再记 `ENABLED=0`，也不用担心应用重启后自己恢复广播）。


## 技术债 / 遗留

- [x] ~~推 GitHub~~ —— 已上 `github.com/leaf8384/fnos-dlna-renderer`，v1.0.0 已发布，CI 用 `debian:12` 容器编译（40 秒通过）
- [x] ~~本地/远端 git 历史分叉~~ —— 2026-10-22 已解决。原因不是真分叉：用 Git Data API 推送时远端 main 被改写成同内容、不同 sha 的提交（`53b3486`），且漏掉了 `docs/app-console-plan.md`。`git fetch` 后用 `git reset --soft origin/main` 对齐，再把漏的文件重新提交上去。
- [x] ~~fpk 未在飞牛应用中心真装验证~~ —— 2026-10-22 已真装通过，详见 `docs/DEPLOY.md` 的「FPK 真装验证记录」。切歌竞态回归三轮全过。
- [ ] `-I` 写死在配置里，换网卡/改枚举后进程 running 但手机搜不到 → 应用化后可根治
- [ ] 跨 Debian 大版本升级后 `libupnp.so.13` SONAME 变化会导致 `error while loading shared libraries`

## 未决策

- Marshall Acton II 待机 20 分钟后能否被 AUX/DLNA 唤醒。官方只对 III 代承诺。唤不醒的话要么加静音保活流，要么接受手动开机。

## 明确不做

- **不重写渲染器**。考虑过 Go / Rust 重写（Rust 的 `symphonia` + `cpal` 能甩掉 GStreamer 和 libupnp 两套依赖），但现有系统稳定、重写零功能增益。只在下述情况重启这个议题：出现修不动的问题，或要做多房间同步 / 逐台 EQ。
- **不接小米音箱**。那是另一个方向（需要米家账密 + 云端下发），有现成的 MiCast / MiCast-Go 可用。
