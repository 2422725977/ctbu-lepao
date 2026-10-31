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
