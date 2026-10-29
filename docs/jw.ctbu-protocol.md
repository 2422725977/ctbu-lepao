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
