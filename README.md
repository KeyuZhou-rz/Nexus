# Nexus

在终端敲 `nexus`，它告诉你**现在该做哪件作业**、要做多久；做完敲 `nexus done`。

```console
$ nexus
现在做  Lab 3: Page Tables
        Operating Systems · 明天 23:59 截止 · 还要 3 小时
        打开「Lab 3: Page Tables」，先做 50 分钟。

之后    Problem Set 5 · Physics · 10 月 22 日 23:59 截止
        Reading Response · History · 10 月 23 日 12:00 截止

nexus start 开始计时 · nexus done 做完了
```

多门课的截止时间散在 Brightspace 各个课程里，每次都得自己翻一遍，再在脑子里排出先做哪件。Nexus 替你做这一步：读课程日历，估出每件事还剩多少工作，然后只给你一件事。你开始、暂停、完成的真实用时会写回本地状态，下次估得更准。

全部数据只存在你的电脑上，默认不调用任何大模型。

## 安装

需要 Python 3.10–3.13，macOS 或 Linux。

```bash
git clone https://github.com/KeyuZhou-rz/Nexus.git
cd Nexus
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 第一次用

1. 在 Brightspace 打开**日历**，点**订阅（Subscribe）**，选**所有课程**，复制订阅链接。一条链接就能覆盖你所有的课。
2. 接入：

   ```bash
   nexus init --feed-url "https://brightspace.nyu.edu/d2l/le/calendar/feed/user/feed.ics?token=..."
   ```

3. 敲 `nexus`。第一次会先拉取课程日历，之后数据超过两小时没更新时也会自动刷新。

只想先看看效果，可以用不需要任何账号的演示数据：

```bash
nexus demo --data-dir /tmp/nexus-demo
nexus --data-dir /tmp/nexus-demo
```

### 可选：Google Calendar

接入后，日历上已确认的安排会从可学时间里扣掉。核心同步只读日历；授权页面还会请求邮件只读权限，那是冻结的简报扩展要用的，默认不会读取。

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 启用 Google Calendar API，创建「桌面应用」OAuth 凭据。
2. 把下载的 JSON 存为 `data/google_client_secret.json`，然后运行 `pip install -e ".[google]"` 和 `nexus-google-auth` 完成授权。
3. `chmod 600 data/google_client_secret.json data/google_token.json`

## 每天怎么用

| 命令 | 作用 |
|---|---|
| `nexus` | 需要时先刷新课程，再给出现在做哪件 |
| `nexus start` | 开始给这件计时 |
| `nexus pause` | 暂停；休息时间不计入用时 |
| `nexus done` | 完成正在做的、或者刚才推荐的那件 |
| `nexus done 关键词` | 完成标题或课程里包含关键词的那件，比如 `nexus done physics`；`start` 也可以带关键词 |
| `nexus add "准备展示" --due 10-23 --hours 2` | 补上日历里没有的任务；`--due` 也接受 `2026-10-23` 和 `"10-23 18:00"`，不写时间就按 23:59 |
| `nexus dashboard` | 在浏览器里看全部任务，修改截止时间和剩余用时 |
| `nexus sync` | 立刻刷新课程日历 |
| `nexus next --json` / `nexus context` | 给脚本或其他工具读的 JSON |

不带关键词时，`start` 和 `done` 作用于正在计时的那件，没有在计时的就是 `nexus` 推荐的那件。同一时间只计时一件，换任务前先 `nexus pause`。

## 它怎么排序

- 已过期的排最前，然后按截止时间从近到远；同时截止的，剩余工作多的在前。
- 剩余工作依次取：你填的剩余时间、实测用时、同类作业的历史用时，最后才用默认估计。
- 设好每周可学时段后，还会比较「剩余工作」和「截止前能学的时间」，做不完的提到最前面。没有可学时段时不会假装算出风险。可学时段目前还没有命令行入口，见路线图。

课程日历只有截止时间，没有提交状态，也可能漏掉课程内容页里的任务。漏掉的用 `nexus add` 补上，交了就 `nexus done`。

## 数据

- 所有状态都在 `data/`（`nexus.db`、`tasks.json`、`feeds.json`），整个目录不进 git。设置 `NEXUS_DATA_DIR` 可以换位置。
- 订阅链接里带有私人 token，只存在 `data/feeds.json`，界面里不会显示。
- 默认同步只读 Brightspace 日历和 Google Calendar；不会打开浏览器，也不会调用大模型。

## 项目结构

```
src/nexus/core/     产品本身：采集 → nexus.db 状态 → 风险与可学时间 → 一个下一步 → 计时/完成反馈
src/nexus/cli/      nexus 命令；把 core 和可选扩展组合起来
src/nexus/...       冻结的扩展：knowledge/、intelligence/、archive_sync/、pipeline.py、memory_*
```

`core` 只 import `core`，由 `tests/test_core_boundary.py` 强制检查；扩展通过参数注入。扩展仍然能用（`nexus --help` 的第二部分），但目前不再继续开发，原因见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 开发

```bash
pip install -e ".[all]" pytest
make test        # 全量测试；每个测试都用临时数据目录，不会碰你的 data/
make dashboard   # 本地启动面板
make practice    # 在 nexus-practice/ 生成演示数据
```

产品定义见 [PRODUCT.md](PRODUCT.md)，路线见 [docs/ROADMAP.md](docs/ROADMAP.md)，测试说明见 [docs/TESTING.md](docs/TESTING.md)，版本记录见 [CHANGELOG.md](CHANGELOG.md)。

## 许可

[MIT](LICENSE) · [隐私说明](PRIVACY.md) · [安全说明](SECURITY.md)
