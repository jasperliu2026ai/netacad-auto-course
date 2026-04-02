# NetAcad Auto Course 🎓

自动化 Cisco Networking Academy (NetAcad) 网课学习的 WorkBuddy Skill。

基于 **Playwright** 浏览器自动化，支持自动登录、视频播放、翻页、答题、进度核验。

## ✨ 功能

| 功能 | 说明 |
|------|------|
| 🔑 自动登录 | 邮箱方式登录 NetAcad（Keycloak SSO） |
| 🎬 视频播放 | 原速完整观看，不跳播 |
| ➡️ 自动翻页 | 检测并点击 Next/Continue/下一步 |
| 📝 智能答题 | 分析选项内容，选择最佳答案 |
| 📊 进度核验 | 每模块完成后返回主页检查 100% |
| 🔔 弹窗处理 | 自动关闭 Cookie/确认弹窗 |
| 💾 断点续学 | 自动保存/恢复已完成章节 |

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install playwright
playwright install chromium
```

### 2. 配置账号

编辑 `scripts/config.json`：

```json
{
  "email": "你的邮箱",
  "password": "你的密码",
  "course_name": "网络信息安全技术",
  "course_url": "",
  "headless": false
}
```

### 3. 运行

```bash
cd scripts
python3 run.py
```

或命令行方式：

```bash
python3 scripts/netacad_auto.py --email "邮箱" --password "密码" --course "课程名"
```

## 📋 学习流程

```
登录 → 进入仪表盘 → 找到课程 → 进入课程
    ↓
┌─────────────────────────────┐
│  单模块循环：                │
│  1. 视频学习（原速完整观看）  │
│  2. 习题作答（智能分析选项）  │
│  3. 翻页推进                 │
│  4. 进度核验（确认 100%）    │
└─────────────────────────────┘
    ↓
全课程完成 → 输出统计报告
```

## 🔧 作为 WorkBuddy Skill 使用

安装到 WorkBuddy 后，直接说：

- "帮我刷网课"
- "自动学习 NetAcad"
- "刷课"

即可触发自动执行。

## ⚠️ 注意事项

- 首次运行建议**不要** headless 模式，观察登录是否正常
- 测验答题为智能选择但不保证100%正确率
- 如遇验证码需手动完成
- 浏览器数据保存在 `scripts/.browser_data/`

## 📁 项目结构

```
netacad-auto-course/
├── SKILL.md              # WorkBuddy Skill 指令
├── README.md             # 本文件
├── .gitignore
└── scripts/
    ├── netacad_auto.py   # 核心自动化引擎
    ├── run.py            # 快捷启动脚本
    └── config.json       # 配置文件模板
```

## License

MIT
