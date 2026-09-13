# 桌面员工 · 第三方平台部署指南（纯静态，域名无 workbuddy）

本目录 `static/` 是纯静态版落地页：表单通过外部收集器收邮箱，不依赖 Python 后端，
可直接挂到任意静态托管平台。部署后域名形如 `xxx.github.io` / `xxx.vercel.app` / `xxx.netlify.app`，
**不含 workbuddy**。

## 第 0 步：填好表单地址（必做）
打开 `static/index.html`，找到脚本里的：
```js
var FORM_ENDPOINT = "https://formspree.io/f/YOUR_FORM_ID";
```
把 `YOUR_FORM_ID` 换成你的真实表单 ID：
- **Formspree（最简单）**：去 https://formspree.io 免费建表单，复制形如 `https://formspree.io/f/abcdwxyz` 的地址填进去。
- **腾讯问卷**：在腾讯问卷建「收集表」→ 用其开放接口地址（需开通 API，企业/个人均可），
  把 `FORM_ENDPOINT` 换成它的提交 URL 即可，字段用 `email`。
- **飞书表单 / 多维表格**：用飞书开放平台「写入记录」接口，同理替换 `FORM_ENDPOINT`。

> 没填会提示「请先在代码中填入你的表单地址」，不会报错。

---

## 方案 A：GitHub Pages（免费，域名 xxx.github.io）
1. 在 GitHub 新建仓库，如 `desktop-employee`。
2. 把 `static/` 里的 `index.html` 传上去（直接放仓库根目录，或放 `docs/` 目录）。
3. 仓库 Settings → Pages → Source 选 `main` 分支（+ `/root` 或 `/docs`）。
4. 等一两分钟，访问 `https://你的用户名.github.io/desktop-employee/`。

## 方案 B：Vercel（免费，域名 xxx.vercel.app）
1. 注册 Vercel，New Project → 导入你的 GitHub 仓库（或拖拽 `static/` 文件夹）。
2. Framework 选「Other」，Output 目录留空（它直接认 index.html）。
3. Deploy → 得到 `https://desktop-employee-xxx.vercel.app`。
4. 想用自己的域名：Settings → Domains 里绑定（需你有域名 + 加 DNS 解析）。

## 方案 C：Netlify（免费，域名 xxx.netlify.app）
1. 打开 https://app.netlify.com/drop ，把 `static/` 文件夹拖进去。
2. 秒出 `https://随机名.netlify.app`。
3. 想自定义域名：Site settings → Domain management 绑定。

---

## 进阶：想保留原版「真后端」（不接外部表单）
如果嫌接 Formspree/腾讯问卷麻烦，也可以把**带后端的完整版**（`../app.py` + `index.html`）原样跑在
支持 Python 的平台上，邮箱直接落库到 `waitlist.csv`，域名同样无 workbuddy：
- **Railway**：新建 Project → 部署这个目录 → 自动识别 Python，`startCmd` 填 `python3 app.py`，域名 `xxx.railway.app`。
- **Render**：New Web Service → 连仓库 → Runtime 选 Python，`start` 填 `python3 app.py`，域名 `xxx.onrender.com`。
- 这两种方式表单不用改（仍用 `/join`），但平台要有 Python 运行时，且 `waitlist.csv` 存在容器磁盘上（重启可能清空，正式可用 SQLite/对象存储）。

---

## 上线后
把最终链接贴进 `../推广文案.md` 里，去小红书 / 知乎 / 视频号发即可。
