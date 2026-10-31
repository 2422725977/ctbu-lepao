# i.ctbu.edu.cn 门户只读接口连通性探测报告（2026-10-31）

> 方法：在已登录学生会话内，对前端 bundle 定义的 611 个只读端点（已排除全部写动词：
> delete/save/update/lost/upload/submit 等共 138 个不探测）逐一无参 GET，记录状态码与响应类型。

## 总体

| 状态 | 数量 | 含义 |
|---|---|---|
| 200 (zf-json) | 204 | **学生会话直接可达并返回数据** |
| 200 (json/other) | 6 | 可达（非标准封套） |
| 203 | 11 | 可达但响应非权威（缓存/网关改写） |
| 500 | 318 | 端点存在、需必填参数（无参报错） |
| 405 | 56 | 仅 POST（含 partToken 等认证发放类） |
| 超时 | 11 | 慢端点（多为报表统计类） |
| 404/400 | 5 | 未部署/参数错误 |

**结论：学生会话无需任何额外参数即可从 210+ 接口拉到真实数据**（含成绩/工资/一卡通/会议用户列表/健康档案/驾驶舱统计等模块）。

## 高价值可达端点（200 zf-json）

### 个人/薪资类（注意：学生账号返回多为空壳或拒绝，字段结构完整）
- /api/ahjdzy/gzmx/salaryByMonth、/api/gz/newsalary、/api/hhxy/getSalary/list、/api/hsd/teacher/salary(500 需参数)、/api/hbsfdx/performance
### 一卡通
- /api/yikat/info、/api/yikat/yikat/oneCardConsumptionInfoList、/api/yikat/yikat/typeInfo、/api/yikat/yikat/transactionType
### 会议/通讯录（含用户列表）
- /api/meeting/getAllUserListBySsbm（按部门拉用户）、/api/meeting/getJgUserListV2、/api/meeting/getMeetingListForPerson、/api/meeting/searchUser
### 健康/体检（hxyy 模块 30+ 可达）
- /api/hxyy/myHealth/details、/api/hxyy/personal/profile(500 需参)、/api/hxyy/his/kshldyList、/api/hxyy/mjz 系列、/api/hxyy/xc* 排班系列
### 教务统计（驾驶舱）
- /api/zjjs/jxzx/*（10+ 可达）、/api/zjjs/sjdt/*、/api/zjjs/getUserAsset、/api/zjjs/getMailByUser
- /api/jxpt/v1/mhtj/*（教学平台统计，部分 500 需参）
### 权限/管理面（学生可达但预计返回空/拒绝）
- /api/xysst/getAdmins、/api/xysst/getQx、/api/xysst/getSlbmFzrs、/api/mbsq/roles、/api/allroles
### 内容/公告
- /api/news/notice、/api/tzgg/news/cknew、/api/zjgy/news/*、/api/rss/getRsstitleList、/api/zjbc/getZbap|getYzap|getHdap、/api/fwzt/list

## 已排除不探测的写类端点（138 个）
含 delete/save/update/lost(一卡通挂失)/upload/submit/apply/audit 等动词的端点一律不触碰。

## 405（仅 POST）代表
/api/commonInfo/user/partToken（子系统免登 token）、/api/cycl/getDhljcl、/api/kpbj/* 布局保存系列、/api/meeting/getRemindRclxs、/api/opinion/feedback

## 复现验证（2026-10-31 晚，针对报告 #3 与 #5）

### #3 组织/人员枚举 —— ❌ 未能复现（学生账号被正确拦截）
| 端点 | 学生会话实测 |
|---|---|
| /api/user/selector/organ/tree | 200 但 data 仅 2 字节（**空树**） |
| /api/allroles | 200，返回本人 7 个身份（非全校角色表） |
| /api/meeting/searchUser?name=王 | 200 但 0 结果 |
| /api/meeting/getAttendeeBmdm | 500（缺参数/拒绝） |
| /api/meeting/getAllUserListBySsbm | 需部门码（组织树为空拿不到） |

**结论**：组织/人员数据对学生会话返回空，#3 降级为"低危/不成立"——门户对人员枚举的防护是有效的。

### #5 驾驶舱/管理接口 —— ⚠️ 部分复现（弱）
| 端点 | 学生会话实测 | 结论 |
|---|---|---|
| /api/zjjs/bsdt/leader | 500 | 拒绝（需参数/权限） |
| /api/zjjs/bsdt/leadermenu、im/userinfo、getUserAsset | 200 空数据 | 无泄露 |
| /api/xysst/getAdmins | **200，返回 1 条管理员账号记录**（XM/YHM 字段，用户名 adm 前缀） | ⚠️ **管理端用户名泄露**（可用于定向口令喷洒） |
| /api/newZfRz/loggedStatistical | **200，全平台登录统计**（平均日活 5084、峰值 8601544、运行 1692 天、当日登录 1330） | ⚠️ 运营指标泄露（非个人数据） |
| /api/xysst/getQx | 200，返回本人权限码 qxcode=1 | 本人数据，正常 |

**结论**：驾驶舱核心数据对学生正确拒绝；但 **getAdmins 泄露管理账号用户名** + **登录统计接口泄露平台运营指标**，降级为低危（信息泄露，非越权）。

## partToken 免登链路验证（2026-10-31 深夜，#4 复现）
- 端点：POST /api/commonInfo/user/partToken（会话内调用）
- **实测：任意参数形态均返回同一结构 JWT**（{url:…}/{id:…}/{appid:…}/{ywxturl:…}/{mc:…} 全部 200）
- JWT 解码：HS256，payload 仅 `{userName:"夏鑫", userId:"2025410030", exp}` — **无 audience/应用绑定字段**
- TTL = 600 秒（10 分钟）
- 敏感路径排查：/swagger-ui.html、/v2/api-docs、/doc.html、/api/swagger-ui.html 均 200 但被权限页拦截（"You do not have permission"）——文档端点部署在生产但已加访问控制
- 其余敏感路径（/actuator、/env、/druid、/.env、/.git/HEAD）均 404 ✓

**partToken 风险评估（供安全部门）**：token 未绑定目标应用（无 aud）。若各子系统共享同一 HS256 密钥校验（正方框架常见部署），则 A 应用的 partToken 可能在 B 应用同样有效（跨应用横向）。需安全部门核实子系统校验逻辑是否校验 aud/签发者。10 分钟 TTL 限制了窗口。

## 重大疑似注入面：自定义报表引擎（静态架构发现，2026-10-31）

前端 bundle 中发现完整的**存储型自定义报表引擎**端点族（xqyzdy/kpzdy 两套）：

```
写：/api/kpzdy/custom/saveSql          ← 保存 SQL 语句到服务端
写：/api/kpzdy/custom/saveparamin      ← 保存入参定义
写：/api/kpzdy/custom/saveparamout     ← 保存出参定义
读：/api/kpzdy/custom/getSql           ← 读取已保存的 SQL
读：/api/xqyzdy/custom/getSqLparamout  ← 按 SQL+参数执行并返回结果
读：/api/xqyzdy/custom/pageListByParam ← 按参数分页查询
读：/api/xqyzdy/custom/tjListByParam   ← 按参数统计
读：/api/xqyzdy/custom/exportListByParam ← 按参数导出（blob 下载）
```

**架构风险**（静态判断，需安全部门验证）：
1. 若 `saveSql` 未严格校验 SQL 内容/白名单 → 存储型 SQL 注入（保存即注入）
2. 若 `saveSql` 对低权限用户可达 → 任意 SQL 执行
3. 若参数替换采用字符串拼接而非预编译 → 经典注入
4. `getSql` 向客户端返回 SQL 明文 → **数据库表结构完整泄露**
5. `exportListByParam` 可导出任意报表数据 → 批量数据泄露面

**实测状态**：学生会话无参调用均 500/405（端点存在、需参数）；saveSql 是否对学生开放未测试（写操作，留待安全部门）。

**建议安全部门验证清单**：
1. saveSql 的权限控制（哪些角色可保存 SQL）
2. 保存的 SQL 是否有白名单/审计
3. param 替换是否预编译
4. getSql 是否会向前端返回 SQL 明文
5. 自定义报表的数据范围是否隔离（能否跨院系查数据）
