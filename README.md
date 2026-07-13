

### How to run the project


### Prerequisites

tabble 分为三层
raw layer: 原始数据, 添加审核字段source_file，sheet_name，load_time
: 清洗后的数据
: 

2. Data Quality Checks
可以扫描出每张表的问题，并且列出问题
格式异常计数（日期、金额、email）
非空约束
值域检查（如 loyalty_score 是否在 1~3）
合法值
关联完整

如果某个字段有问题，会被标记为 dirty，dirty 的字段会被记录在 dirty_fields 中，dirty 的行会被记录在 dirty_rows 中

检查结构：字段名、类型是否合理，是否混合类型
检查完整性：空值率、缺失模式
检查唯一性：主键/候选键是否重复
检查合法性：值是否在业务允许范围内（如 loyalty_score 只能 1-3）
检查一致性：同一概念是否多种写法（DE vs Deutschland）
检查格式：日期、金额、email 是否统一格式
检查异常/离群：负金额、未来日期、超大值等
检查可关联性：orders 的 email 能否匹配 customer（join 命中率）




日期格式混乱（2021/9/1, 2/ Aug/, 2021年9月18日）
金额含 € 或不含符号
gender 混用（male/female/m/f）
country 混用（DE/Deutschland）
survey 第一列混合 order number 和 email
city 与 zip 混在一起（Düsseldorf 40239）
email 大小写不一致、疑似重复实体

3. 每张表具体的清洗方案
