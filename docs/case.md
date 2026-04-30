重写优化
信息
PawSQL的重写优化引擎提供丰富的SQL重写优化，推荐语义等价，但执行效率更高的SQL语句.

正确性规则
1.ALL修饰的子查询重写优化
规则描述
假设通过下面的SQL来获取订单系统关闭后注册的用户

select * from customer where c_regdate > all(select o_orderdate from orders)

如果子查询的结果中存在NULL，这个SQL永远返回为空。正确的写法应该是在子查询里加上非空限制，或使用max/min的写法

select * from customer where c_regdate > (select max(o_custkey) from orders)

PawSQL推荐采用第二种写法，可以通过max/min重写进一步优化SQL，获取该优化的更详细信息。

触发条件
ALL修饰的子查询条件
2. IN可空子查询可能导致结果集不符合预期
规则描述
对于以下想要查询没有订单用户的SQL，

select * from customer where c_custkey not in (select o_custkey from orders)

如果子查询的结果集里有空值，这个SQL永远返回为空。正确的写法应该是在子查询里加上非空限制，即

select * from customer where c_custkey not in (select o_custkey from orders where o_custkey is not null)

触发条件
存在IN子查询条件
IN子查询的选择列取值可以为NULL
3. NPE重写
规则描述
SQL的NPE(Null Pointer Exception)问题是指在SQL查询中,当聚合列全为NULL时,SUM、AVG等聚合函数会返回NULL,这可能会导致后续的程序出现空指针异常。譬如对于下面的SQL：

select sum(t.b) from (values row(1,null)) as t(a,b);

可以使用如下方式避免NPE问题:

SELECT IFNULL(SUM(t.b), 0) from (values row(1,null)) as t(a,b);

这会返回0而不是NULL,避免了空指针异常。

Oracle:NVL(); SQL Server和MS Access:ISNULL(); MySQL:IFNULL()或COALESCE();

触发条件
SUM或AVG聚集函数
聚集函数的参数可能全为NULL, 包括
参数是列，列定义可以为空
参数是表达式，表达式可以为空
列定义不可为空，但是是外连接的内表，结果可能为空
4. 禁止使用=NULL判断空值
规则描述
= null并不能判断表达式为空,= null总是被判断为假。判断表达式为空应该使用is null.

case expr when nulll也并不能判断表达式为空, 判断表达式为空应该case when expr is null。在where/having的筛选条件的错误写法还比较容易发现并纠正，而在藏在case 语句里使用null值判断就比较难以被发现。

触发条件
语句中存在 = null 或是case when expr is null判断逻辑
性能优化规则
1. 显式禁止结果字段排序
规则描述
在MySQL的早期版本中，即使没有order by子句，group by默认也会按分组字段排序，这就可能导致不必要的文件排序，影响SQL的查询性能。可以通过添加order by null来强制取消排序，禁用查询结果集的排序；PawSQL识别并进行了重写。

譬如下面的例子中

SELECT l_orderkey, sum(l_quantity) 
FROM lineitem
GROUP BY l_orderkey;

在MySQL 5.x版本中，group by l_orderkey会引起默认排序, 可以通过添加order by null来避免该排序。

SELECT l_orderkey, sum(l_quantity) 
FROM lineitem
GROUP BY l_orderkey
ORDER BY NULL;

触发条件
MySQL数据库，版本低于8.0

存在分组字段，且无排序字段

2. COUNT标量子查询重写
规则描述
对于使用COUNT标量子查询来进行判断是否存在，可以重写为EXISTS子查询，从而避免一次聚集运算。譬如对于如下的SQL，

select * from customer where (select count(*) from orders where c_custkey=o_custkey) > 0

可以重写为,

select * from customer where exists(select 1 from orders where c_custkey=o_custkey)

规则描述
数据库可以利用索引的有序性来避免ORDER子句中列的排序，从而提升SQL的性能。但是如果ORDER字段是一个表达式或函数，则可能无法利用索引来进行排序。

触发条件
存在COUNT标量子查询>0条件
3. 无条件的DELETE建议重写为TRUNCATE
规则描述
没有查询条件或查询条件恒真的DELETE语句会删除表中的所有数据。DELETE语句需要写大量日志，以便进行事务回滚及主备同步。对于大表而言，可能会导致数据库的锁定和事务阻塞，同时会占用大量的日志空间。如果确认表中的数据不再需要，可以通过TRUNCATE表了代替DELETE语句。TRUNCATE比DELETE语句更快，因为它不会记录每个删除的行，而是直接将表清空并释放空间。

delete from lineitem

重写为

truncate lineitem

触发条件
没有条件或条件恒真的DELETE语句
4. 隐式类型转换导致索引失效
规则描述
当条件表达式的数据类型不同时，在查询执行过程中会进行一些隐式的数据类型转换。类型转换有时会应用于条件中的常量，有时会应用于条件中的列。当在列上应用类型转换时，在查询执行期间无法使用索引，可能导致严重的性能问题。譬如对于以下的SQL，

select count(*) from ORDERS where O_ORDERDATE = current_date();

如果O_ORDERDATE列的数据类型是CHAR(16)，那么O_ORDERDATE上的索引将不会被使用，导致全表扫描。解决方案通常有两个，一是ALTER TABLE改变O_ORDERDATE的数据类型，二是把current_date强制换换为CHAR类型（PawSQL提供该重写建议）。

 select count(*) ORDERS where ORDERS.O_ORDERDATE = cast(current_date() as CHAR(16));

触发条件
条件表达式是个过滤条件，且是个可索引的过滤条件
过滤条件两边的数据类型不一样
根据数据库类型转换的优先级，数据库会优先转换列而非常量
5. 子查询中的DISTINCT消除
规则描述
对于仅进行存在性测试的子查询,如果子查询包含DISTINCT通常可以删除,以避免一次去重操作，譬如

IN子查询:
SELECT * FROM customer WHERE c_custkey IN (SELECT DISTINCT o_custkey FROM orders);

可以简化为:

SELECT * FROM customer WHERE c_custkey IN (SELECT o_custkey FROM orders);

触发条件
使用IN/EXISTS子查询进行存在性判断
子查询中存在DISTINCT/DISTINCT/UNIQUE关键字
6. EXISTS子查询转换为表连接
规则描述
EXISTS子查询向外层查询返回一个布尔值,表示是否存在满足条件的行。满足一定条件的EXISTS 子查询可以转换为 JOIN，从而可以让数据库优化器对驱动表的选择有更多的选择，从而生成更优的查询计划。

譬如对于如下的查询，

select * from lineitem l where exists (select * from part p where p.p_partkey=l.l_partkey and p.p_name = 'a')


如果子查询对于每一个l.l_partkey,都至多返回一行记录（即在等值条件的列上(p_partkey，p_name)有一个唯一性约束），则此子查询可以重写为如下的形式:

select l.* from lineitem as l, part as p where p.p_partkey = l.l_partkey and p.p_name = 'a'

触发条件
EXISTS子查询条件由AND和其他条件关联
EXISTS子查询无分组无LIMIT
EXISTS子查询结果集返回UNIQUE的行
EXISTS子查询和外查询关联方式为等值关联
7. 过滤谓词下推
规则描述
滤条件下推（FPPD）通过尽可能的 “下压” 过滤条件至SQL中的内部查询块，提前过滤掉部分数据, 减少中间结果集的大小，进而减少后续计算需要处理的数据量，进而提升SQL执行性能，FPPD属于重写优化。

譬如如下的案例中，在外查询有一个条件nation = 100，可以下压到personDT子查询中：

select *
from (select c_nationkey nation, 'C' as type, count(1) num
      from customer
      group by nation 
      union 
      select s_nationkey nation, 'S', count(1) num
      from supplier
      group by nation) as person
where nation = 100

重写之后的SQL如下:

select *
from (select c_nationkey nation, 'C' as type, count(1) num
      from customer
      where c_nationkey = 100
      group by nation 
      union 
      select s_nationkey nation, 'S', count(1) num
      from supplier
      where s_nationkey = 100
      group by nation) as person

触发条件
过滤条件是个AND过滤条件（非连接条件）

过滤条件的字段来自FROM子查询（如果是视图，应该被视图定义的SQL替换掉）

该子查询没有被 查询折叠优化消除掉

该子查询本身没有LIMIT子句

该子查询本身没有rownum或rank等窗口函数

8. 索引列上的运算导致索引失效
规则描述
在索引列上的运算将导致索引失效，容易造成全表扫描，产生严重的性能问题。所以需要尽量将索引列上的运算转换到常量端进行，譬如下面的SQL。

select * from tpch.orders where adddate(o_orderdate,  INTERVAL 31 DAY) =date '2019-10-10'  

adddate函数将导致o_orderdate上的索引不可用，可以将其转换成下面这个等价的SQL，以便使用索引提升查询效率。

select * from tpch.orders where o_orderdate = subdate(date '2019-10-10' , INTERVAL 31 DAY);

PawSQL可以帮助转换大量的函数以及+、-、*、/运算符相关的操作。点击获取该优化的更详细信息。

触发条件
过滤条件是个AND过滤条件（非连接条件）

过滤条件是个可索引条件

在索引列上存在计算或函数

9. 避免GROUPBY字段来自不同表
规则描述
如果分组字段来自不同的表，数据库优化器将没有办法利用索引的有序性来避免一次排序。如果WHERE或是HAVING子句里存在等值条件，PawSQL可以排序或分组字段进行替换，使其来自同一张表，从而能够利用索引来避免一次排序。譬如下面的查询

select o_custkey, c_name, sum(o.O_TOTALPRICE) from customer c, orders o where o_custkey = c_custkey group by o_custkey, c_name


分组字段o_custkey, c_name来自两个表，且存在过滤条件o_custkey = c_custkey，可以重写为

select c_custkey, c_name, sum(o.O_TOTALPRICE) from customer c, orders o where o_custkey = c_custkey  group by c_custkey, c_name


触发条件
GROUPBY字段来自不同表

过滤条件是个可索引条件

在索引列上不存在计算或函数

10. HAVING条件下推到WHERE
从逻辑上，HAVING条件是在分组之后执行的，而WHERE子句上的条件可以在表访问的时候（索引访问）,或是表访问之后、分组之前执行，这两种条件都比在分组之后执行代价要小。

考虑下面的例子，

select c_custkey, count(*) from customer group by c_custkey having c_custkey < 100

重写后的SQL为，

select c_custkey, count(*) from customer where c_custkey < 100 group by c_custkey

触发条件
HAVING子句中不存在聚集函数
11. IN子查询重写优化
IN子查询是指符合下面形式的子查询，IN子查询可以改写成等价的相关EXISTS子查询或是内连接，从而可以产生一个新的过滤条件，如果该过滤条件上有合适的索引，或是通过PawSQL索引推荐引擎推荐合适的索引，可以获得更好的性能。

(expr1, expr2...) [NOT] IN (SELECT expr3, expr4, ...)

IN子查询重写为EXISTS
譬如下面的IN子查询语言是为了获取最近一年内有订单的用户信息，

select * from customer where c_custkey in (select o_custkey from orders where O_ORDERDATE>=current_date - interval 1 year)


它可以重写为exists子查询，从而可以产生一个过滤条件（c_custkey = o_custkey）：

select * from customer where exists (select * from orders where c_custkey = o_custkey and O_ORDERDATE>=current_date - interval 1 year)


IN子查询重写为内关联
如果子查询的查询结果是不重复的，则IN子查询可以重写为两个表的关联，从而让数据库优化器可以规划更优的表连接顺序，也可以让PawSQL推荐更好的优化方法。

譬如下面的SQL， c_custkey是表customer的主键，

select * from orders where o_custkey in (select c_custkey from customer where c_phone like '139%')

则上面的查询语句可以重写为

select orders.* from orders, customer where o_custkey=c_custkey and c_phone like '139%'

点击获取该优化的更详细信息。

触发条件
IN子查询不是反向条件
如果子查询的结果集是不重复的，可以重写为内关联
12. 表连接消除
规则描述
连接消除（Join Elimination）通过在不影响最终结果的情况下从查询中删除表，来简化SQL以提高查询性能。通常，当查询包含主键-外键连接并且查询中仅引用主表的主键列时，可以使用此优化。内连接和外连接都可以用于此重写优化。

内连接消除的案例
select o.* from orders o inner join customer c on c.c_custkey=o.o_custkey

订单表（orders）和客户表（customer）关联，且c_custkey是客户表的主键，那么客户表可以被消除掉，重写后的SQL如下：

select * from orders where o_custkey

外连接消除的案例
select o_custkey from orders left join customer on c_custkey=o_custkey

客户表可以被消除掉，重写后的SQL如下：

select orders.o_custkey from orders

触发条件
查询包含主键-外键连接
查询中仅引用主表的主键列
13. LIMIT下推至UNION分支
规则描述
Limit子句下推优化通过尽可能的 “下压” Limit子句，提前过滤掉部分数据, 减少中间结果集的大小，减少后续计算需要处理的数据量, 以提高查询性能。

譬如如下的案例，在外查询有一个Limit子句，可以将其下推至内层查询执行：

select *
from (select c_nationkey nation, 'C' as type, count(1) num
      from customer
      group by c_nationkey 
      union 
      select s_nationkey nation, 'S' as type, count(1) num
      from supplier
      group by nation) as nation_s
order by nation limit 20, 10

重写之后的SQL如下:

select *
from (
(select customer.c_nationkey as nation, 'C' as `type`, count(1) as num
        from customer
        group by customer.c_nationkey
        order by customer.c_nationkey limit 30) 
       union 
(select supplier.s_nationkey as nation, 'S' as `type`, count(1) as num
  from supplier
  group by supplier.s_nationkey
  order by supplier.s_nationkey limit 30)) as nation_s
order by nation_s.nation limit 20, 10

触发条件
外查询有一个LIMIT子句
外查询没有GROUP BY子句
外查询的FROM只有一个表引用，且是一个子查询
外查询没有其他条件
子查询为单个查询或是UNION/UNION ALL连接的多个子查询（或者是一个外连接的外表）
OFFSET的值小于指定阈值
21. MAX/MIN子查询重写
规则描述
对于使用MAX/MIN的子查询，

select * from customer where c_custkey = (select max(o_custkey) from orders)

可以重写为以下的形式，从而利用索引的有序来避免一次聚集运算，

select * from customer where c_custkey = (select o_custkey from orders order by o_custkey desc null last limit 1)


获取该优化的更详细信息。

触发条件
SQL中存在MAX/MIN的标量子查询
14. ORDER子句重排序优化
规则描述
如果一个查询中既包含来自同一个表的排序字段也包含分组字段，但字段顺序不同，可以通过调整分组字段顺序，使其和排序字段顺序一致，这样数据库可以避免一次排序操作。

考虑以下两个SQL, 二者唯一的不同点是分组字段的顺序（第一个SQL是c_custkey, c_name, 第二个SQL是c_name,c_custkey），由于分组字段中不包括grouping set/cube/roll up等高级grouping操作，所以两个SQL是等价的。但是二者的执行计划及执行效率却不一样，因此可以考虑将第一个SQL重写为第二个SQL。

select o_custkey, o_orderdate, sum(O_TOTALPRICE)
from orders 
group by o_custkey,o_orderdate
order by o_orderdate;

重写为：

select o_custkey, o_orderdate, sum(o_totalprice)
from orders 
group by o_orderdate,o_custkey
order by o_orderdate;

触发条件
在一个QueryBlock中存在成员大于1的order子句及group子句
子句中引用的是同一个数据表中的列且无函数或计算
order子句中的列是group子句的真子集
order子句不是group子句的前缀
15. OR条件的SELECT重写
规则描述
如果使用OR条件的查询语句，数据库优化器有可能无法使用索引来完成查询。譬如，

select * from lineitem where l_shipdate = date '2010-12-01' or l_partkey<100

如果这两个字段上都有索引，可以把查询语句重写为UNION或UNION ALL查询，以便使用索引提升查询性能。

select * from lineitem where l_shipdate = date '2010-12-01' 
union select * from lineitem where l_partkey<100

如果数据库支持INDEX MERGING，也可以调整数据库相关参数启用INDEX MERGING优化策略来提升数据库性能。获取该优化的更详细信息。

触发条件
OR连接的条件必须是可以利用索引的
重写后的UNION语句估算代价比原SQL小
如果OR分支的条件是互斥的，那么重写为UNION ALL
16. OR条件的UPDELETE重写
规则描述
如果有使用OR条件的UPDATE或DELETE语句，数据库优化器有可能无法使用索引来完成操作。

delete from lineitem where l_shipdate = date '2010-12-01' or l_partkey<100

如果这两个字段上都有索引，可以把它重写为多个DELETE语句，利用索引提升查询性能。

delete from lineitem where l_shipdate = date '2010-12-01';
delete from lineitem where l_partkey<100;

触发条件
SQL为UPDATE或DELETE语句
UPDATE或DELETE语句存在OR条件
OR条件的各个分支都可以索引
17. 子查询中没有LIMIT的排序消除
规则描述
如果子查询没有LIMIT子句，那么子查询的排序操作就没有意义，可以将其删除而不影响最终的结果。一些案例如下：

EXISTS子查询
select * from lineitem l where exists (select * from part p where p.p_partkey=l.l_partkey and p.p_name = 'a' order by p_name )


可以重写为

select * from lineitem l where exists (select * from part p where p.p_partkey=l.l_partkey and p.p_name = 'a')


触发条件
子查询存在ORDER子句
子查询中没有LIMIT子句
18. 避免ORDERBY字段来自不同表
规则描述
如果排字段来自序不同的表，数据库优化器将没有办法利用索引的有序性来避免一次排序。如果WHERE或是HAVING子句里存在等值条件，PawSQL可以排序或分组字段进行替换，使其来自同一张表，从而能够利用索引来避免一次排序。譬如下面的查询

select * from customer c, orders o where o_custkey = c_custkey order by o_custkey, c_name

排序字段o_custkey, c_name来自两个表，且存在过滤条件o_custkey = c_custkey，可以重写为

select * from customer c, orders o where o_custkey = c_custkey  order by c_custkey, c_name

触发条件
ORDER字段来自不同表

过滤条件是个可索引条件

在索引列上不存在计算或函数

19. 外连接优化
规则描述
外连接优化指的是满足一定条件（外表具有NULL拒绝条件）的外连接可以转化为内连接，从而可以让数据库优化器可以选择更优的执行计划，提升SQL查询的性能。

考虑下面的例子，

select c_custkey from orders left join customer on c_custkey=o_custkey where C_NATIONKEY  < 20

C_NATIONKEY < 20是一个customer表上的NULL拒绝条件，所以上面的左外连接可以重写为内连接，

select c_custkey from orders inner join customer on c_custkey=o_custkey where C_NATIONKEY  < 20

获取该优化的更详细信息。

触发条件
对于SQL，

SELECT * T1 FROM T1 LEFT JOIN T2 ON P1(T1,T2) WHERE P(T1,T2) AND R(T2) 

如果R(T2) 是一个空拒绝条件条件(NFC)，那么以上的外连接可以转化为内连接，即

SELECT * T1 FROM T1 JOIN T2 ON P1(T1,T2) WHERE P(T1,T2) AND R(T2) 

这样，优化器可以先应用R(T2) ，获取非常小的结果集，然后再和T1进行关联。

20. 投影下推(PROJECTION PUSHDOWN)
规则描述
投影下推指的通过删除DT子查询中无意义的列（在外查询中没有使用），来减少IO和网络的代价，同时提升优化器在进行表访问的规划时，采用无需回表的优化选项的几率。

考虑下面的例子，

SELECT count(1) FROM (SELECT c_custkey, avg(age) FROM customer group by c_custkey) AS derived_t1;

重写后的SQL为，

SELECT count(1) FROM (SELECT 1 FROM customer group by c_custkey) AS derived_t1;

获取该优化的更详细信息。

触发条件
内层选择列表中存在外层查询块没有使用的列
21. 修饰子查询重写优化
规则描述
ANY/SOME/ALL修饰的子查询来源自SQL-92 标准, 通常用于检查某个值与子查询返回的全部值或任意值的大小关系。使用ANY/SOME/ALL修饰的子查询执行效率低下,因为需要对子查询的结果集逐行进行比较,随着结果集大小增加而线性下降。可以通过查询重写的方式提升其执行效率。

譬如对于下面的SQL：

select * from orders where o_orderdate < all(select o_orderdate from orders where o_custkey > 100)

对于MySQL，可以重写为

select * from orders where o_orderdate < (select o_orderdate from orders where o_custkey > 100 order by o_orderdate asc limit 1) 


对于PostgreSQL或Oracle，则可以重写为

select * from orders where o_orderdate < (select o_orderdate from orders where o_custkey > 100 order by o_orderdate asc nulls first limit 1)


触发条件
SQL中存在ANY/SOME/ALL修饰的子查询
22. 查询折叠(QUERY FOLDING)
规则描述
查询折叠指的是把视图、CTE或是DT子查询展开，并与引用它的查询语句合并，来减少序列化中间结果集，或是触发更优的关于表连接规划的优化技术。

考虑下面的例子，

SELECT * FROM (SELECT c_custkey, c_name FROM customer) AS derived_t1;

重写后的SQL为，

SELECT c_custkey, c_name FROM customer

获取该优化的更详细信息。

触发条件
PawSQL优化引擎针对不同的SQL语法结构，支持两种查询折叠的优化策略。其中第一种查询折叠的优化，MySQL 5.7以及PostgreSQL 14.0以上的版本都在优化器内部支持了此类优化；而第二类查询折叠的优化，在最新的MySQL及PostgreSQL版本中都没有支持。

查询折叠类型 I

在视图本身中,没有distinct关键字

在视图中没有聚集函数或窗口函数

在视图本身中,没有LIMIT子句

在视图本身中,没有UNION或者UNION ALL

在外部查询块中,被折叠的视图不是外连接的一部分。

查询折叠类型 II

在外部查询块中,视图是唯一的表引用

在外部查询块中,没有分组、聚集函数和窗口函数

在视图内部没有使用窗口函数

23. SATTC重写优化
规则描述
SAT-TC(SATisfiability-Transitive Closure) 重写优化是指分析一组相关的查询条件，去发现是否有条件自相矛盾、简化或是推断出新的条件，从而帮助数据库优化器选择更好的执行计划，提升SQL性能。

考虑下面的例子，

select c.c_name FROM customer c where c.c_name = 'John' and c.c_name = 'Jessey'

由于条件自相矛盾，所以重写后的SQL为，

select c.c_name from customer as c where  1 = 0 

获取该优化的更详细信息。

触发条件
谓词间存在矛盾(例如 c_custkey=1 AND c_custkey=0),或者

可以从谓词集中推断出新的谓词(例如 c_custkey=1 AND c_custkey=o_custkey 意味着 o_custkey=1)。

谓词可以简化(例如 c_custkey <> c_custkey or c_name = 'b' 可以简化为 c_name = 'b')

24. 选择列标量子查询解关联（RuleSelectSSQRewrite）
规则描述
标量子查询返回单行单列的一个值，它可以出现在SQL中任何单值出现的地方。对于相关标量子查询，PawSQL对尝试对其进行解关联，以便提升其性能。同时，如果主体结构相似的多个子查询出现在选择列表中，PawSQL在解关联时会将其合并，从而减少计算。

譬如如下的标量子查询

select c_custkey, 
  (select SUM(o_totalprice)
    from ORDERS
    where o_custkey = c_custkey and o_orderdate = '2020-04-16') as total,
  (select count(*)
    from ORDERS
    where o_custkey = c_custkey and o_orderdate = '2020-04-16') as cnt
from CUSTOMER

将会被重写为：

select /*QB_1*/ c_custkey, SUM_ as total, count_ as cnt
from CUSTOMER left outer join (
    select o_custkey, SUM(o_totalprice) as SUM_,count(*) as count_
    from ORDERS
    where o_orderdate = '2020-04-16'
    group by o_custkey) as SQ on o_custkey = c_custkey

25. 条件标量子查询解关联（RulePredicateSSQRewrite）
规则描述
标量子查询返回单行单列的一个值，它可以出现在SQL中任何单值出现的地方。对于出现在WHERE和HAVING子句中的相关标量子查询，PawSQL对尝试对其进行解关联，以便提升其性能。

譬如如下的标量子查询

select c_custkey
from CUSTOMER
where 1000000 < (select SUM(o_totalprice)
           from ORDERS
           where o_custkey = c_custkey and o_orderdate = '2020-04-16')

将会被重写为：

select /*QB_2*/ c_custkey
from CUSTOMER, (
    select /*QB_1*/ SUM( o_totalprice) as SUM_,  o_custkey
    from ORDERS
    where o_orderdate = cast('2020-04-16' as DATE)
    group by o_custkey) as SQ
where 1000000 < SUM_ and o_custkey = CUSTOMER.c_custkey

26. 视图展开（RuleViewResolvingRewrite）
规则描述
视图展开是指将查询中引用的视图替换为其定义的过程。这种优化技术可以让查询优化器（包括PawSQL优化引擎和数据库优化器）有更多的优化机会,从而可能产生更高效的执行计划。

譬如

-- 创建一个视图
CREATE VIEW high_salary_employees AS
SELECT employee_id, name, department_id
FROM employees
WHERE salary > 50000;

-- 使用视图的查询
SELECT h.name, d.department_name
FROM high_salary_employees h
JOIN departments d ON h.department_id = d.department_id
WHERE h.employee_id < 1000;

将会被重写为：

SELECT h.name, d.department_name
FROM high_salary_employees h
JOIN (SELECT employee_id, name, department_id
FROM employees
WHERE salary > 50000) d ON h.department_id = d.department_id
WHERE h.employee_id < 1000;