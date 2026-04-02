---
name: netacad-auto-course
description: |
  自动化 Cisco Networking Academy (NetAcad) 网课学习。使用 Playwright 浏览器自动化完成：视频播放（2倍速）、翻页/下一步、章节测验答题、签到打卡。
  当用户提到以下场景时触发：
  - "刷网课"、"刷课"、"自动学习"、"自动上课"
  - "netacad"、"NetAcad"、"Cisco 网课"、"思科网课"
  - "自动播放视频"、"自动翻页"、"自动答题"
  - 任何涉及 netacad.com 或 skillsforall.com 的学习自动化需求
---

# NetAcad 标准化学习 Skill

基于 Playwright 的 Cisco NetAcad 网课自动学习工具。按照 **视频学习 → 习题作答 → 进度核验** 的标准流程逐模块推进。

## 环境准备

```bash
pip install playwright
playwright install chromium
```

## 配置

编辑 `scripts/config.json`：

```json
{
  "email": "登录邮箱",
  "password": "登录密码",
  "course_name": "课程名称（如：网络信息安全技术）",
  "course_url": "课程直达链接（可选）",
  "headless": false
}
```

## 启动

```bash
cd scripts && python3 run.py
```

或命令行方式：

```bash
python3 scripts/netacad_auto.py --email "邮箱" --password "密码" --course "课程名"
```

## 学习流程

### 阶段1：登录
- 邮箱方式登录（非 Google SSO）
- 通过 Keycloak 认证跳转
- 支持分步登录（先邮箱后密码）

### 阶段2：进入课程
- 在仪表盘查找指定课程名称
- 支持关键词模糊匹配
- 支持直达链接

### 阶段3：模块循环

对每个模块执行：

1. **视频学习** — 原速完整观看，不跳播不倍速，定期检查播放状态
2. **习题作答** — 分析题目和选项内容，智能选择最佳答案，提交后查看反馈
3. **翻页推进** — 自动点击 Next/Continue/下一步
4. **进度核验** — 返回课程主页检查模块进度是否 100%

### 阶段4：全课程完成
- 循环推进直到所有模块 100%
- 自动打印学习统计报告

## 截图调试

运行过程中自动保存截图到 `scripts/screenshots/`：
- `dashboard.png` — 仪表盘页面
- `course_round_N.png` — 每轮模块检查
- `quiz_N.png` — 每道测验题目
- `login_*.png` — 登录问题排查

## 故障排查

| 问题 | 处理 |
|------|------|
| 登录失败 | 查看 screenshots/login_*.png |
| 找不到课程 | 在 config.json 设置 course_url 直达链接 |
| 视频不播放 | 检查浏览器是否被拦截 |
| 页面卡住 | 连续无操作 15 次后自动跳过 |
