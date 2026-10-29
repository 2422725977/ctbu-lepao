# jw.ctbu.edu.cn 教务系统（强智科技）接口协议笔记

> 2026-10-30 被动协议分析（用户本人会话内只读观察）。与 portal 里对接的正方 jwglxt（cloud.zfsoft.com）是并存的另一套系统。

## 架构与认证
- 系统：强智科技教务（路径特征 /jsxsd/、Logon.do、.jsp/.do）
- 认证：CAS 会话直通（i.ctbu.edu.cn 登录后访问 /jsxsd/framework/xsMain.jsp 免登放行；桥接入口 /Logon.do?method=logonFromJsxsd）
- 会话：JSESSIONID（Java）+ LB 粘性 Cookie `jw=real_jwc_jw<N>`（节点 jw4/jw9 轮换）
- 响应头有个彩蛋：`Set-Cookie: name=value`（占位符 Cookie，写死在配置里）

## 功能树（学生端，来自 xsMain_new.jsp 的 kjcdShow 菜单树）
| 功能 | URL |
|---|---|
| 我的课表 | /jsxsd/xskb/xskb_list.do |
| 考试成绩查询 | /jsxsd/kscj/cjcx_frm |
| 选课中心 | /jsxsd/xsxk/xklc_list |
| 考试安排查询 | /jsxsd/xsks/xsksap_query |
| 学生评教 | /jsxsd/xspj/xspj_find.do |
| 缓考申请 | /jsxsd/kscj/hksq_query |
| 等级考试报名 | /jsxsd/xsdjks/xsdjks_list |
| 培养方案明细 | /jsxsd/pyfa/topyfamx |

## 协议特征
- 服务端渲染 JSP + 表单 POST（非 JSON API）；查询页多为 `.do` + `gnmkdm`（功能码）参数风格
- 菜单机制：`kjcdShow(模块,子模块,页面,URL)` JS 转跳
- 学生主框架：/jsxsd/framework/xsMain.jsp（外壳）+ xsMain_new.jsp?t1=1（内嵌课表视图）
- 课表数据直接渲染在 xsMain_new.jsp 里（周次/节次表格）

## 自动化对接要点
- 会话：CAS 登录态或直接复用 JSESSIONID；LB 用 jw Cookie 保持节点
- 全部 GET 即可拉数据（成绩/课表/考试安排都是查询页 + POST 查询参数）
- 强智常见坑：查询 .do 需要 xnm（学年）/xqm（学期，值为 3/12/16…的位掩码风格编码）参数

## 深挖补充（2026-10-30，会话内只读抓取）

### 成绩查询三级链
1. `/jsxsd/kscj/cjcx_frm` — 外壳页（frameset）
2. `/jsxsd/kscj/cjcx_query` — 查询表单（内嵌 iframe）
   - `kksj` 开课学期 select（值形如 `2026-2027-1`；下拉含未来学期 2026-2027-2）
   - `kcxz` 课程性质（11 辅修/14 专业核心/15 专业拓展/16 通识选修…）
   - `kcmc` 课程名称、`xsfs` 显示方式（all 全部/max 最好成绩）
3. **`/jsxsd/kscj/cjcx_list?kksj=<学期>` — 数据端点（GET 直拼，无需 POST）**

### 成绩表结构（cjcx_list，16 列）
序号 | 开课学期 | 课程编号 | 课程名称 | 分组名 | 成绩 | 成绩标识 | 学分 | 总学时 | 绩点 | 补重学期 | 考核方式 | 考试性质 | 课程属性 | 课程性质 | 通选课类别

### 课表接口
`/jsxsd/xskb/xskb_list.do?gnmkdm=N2151`（gnmkdm=功能码，N2151=我的课表；返回 ~113KB 完整渲染页）

### 隐藏端点（框架页 HTML 挖出）
- `/jsxsd/xspj/getXshxToken.do` — 评教防重放 token
- `/jsxsd/portal/queryDetails.do` — 门户详情查询
- `/jsxsd/framework/main_index_loadkb.jsp` — 课表异步加载
- `/jsxsd/framework/main_index_loadtzgg.jsp` / `main_index_xyxxgg.jsp?id=` — 通知公告/学院公告

### ⚠️ 关键协议坑：会话失效的误导性报错
直接 fetch 教务端点（未经顶层页面导航建立会话）会返回：
CAS 跳转 + 「该帐号不存在或密码错误,请联系管理员!」错误页——**这是会话/LB 节点问题，不是密码错误**。
自动化对接必须先走一次顶层页面加载（或完整 CAS 桥接），再用同 Cookie 栈请求数据端点。

### 会话特征
- Cookie：`jw=real_jwc_jw<N>`（LB 节点，会出现重复 Cookie）+ JSESSIONID（HttpOnly）
- 节点轮换会导致 fetch 与页面导航落在不同节点 → 会话"丢失"假象
