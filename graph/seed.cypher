// ============================================================
// 智家（SmartHome）虚构商品知识图谱 — 种子数据
// 节点: Product, Protocol, Accessory, Policy
// 关系: COMPATIBLE_WITH, SUPPORTS, REQUIRES, COVERED_BY
// ============================================================

// --- 清理本模块拥有的节点及关系（不触碰其他应用数据）---
MATCH (n:Product)    DETACH DELETE n;
MATCH (n:Protocol)   DETACH DELETE n;
MATCH (n:Accessory)  DETACH DELETE n;
MATCH (n:Policy)     DETACH DELETE n;

// --- 商品 ---
CREATE (:Product {name: "Cam-A1",   category: "摄像头",   description: "智能家居摄像头，支持夜视与运动检测"});
CREATE (:Product {name: "Hub-Z1",   category: "网关",     description: "智能家居中枢网关，支持多协议接入"});
CREATE (:Product {name: "Sensor-T1", category: "传感器",  description: "温湿度传感器，低功耗设计"});
CREATE (:Product {name: "Lock-D1",  category: "门锁",     description: "智能门锁，支持指纹与密码开锁"});
CREATE (:Product {name: "Light-B1", category: "照明",     description: "智能灯泡，支持亮度和色温调节"});
CREATE (:Product {name: "Plug-P1",  category: "插座",     description: "智能插座，支持定时与电量统计"});

// --- 协议 ---
CREATE (:Protocol {name: "WiFi",      band: "2.4GHz/5GHz"});
CREATE (:Protocol {name: "Zigbee",    band: "2.4GHz"});
CREATE (:Protocol {name: "Bluetooth", band: "2.4GHz"});
CREATE (:Protocol {name: "Thread",    band: "2.4GHz"});

// --- 配件 ---
CREATE (:Accessory {name: "Mount-K1",  type: "支架", compatible_with: "Cam-A1"});
CREATE (:Accessory {name: "Battery-B2", type: "电池", compatible_with: "Hub-Z1"});

// --- 保修政策 ---
CREATE (:Policy {name: "Standard-1Y",  duration: "1年", description: "一年标准保修，非人为损坏免费维修"});
CREATE (:Policy {name: "Premium-3Y",   duration: "3年", description: "三年尊享保修，含意外损坏与上门取送"});
CREATE (:Policy {name: "Extended-2Y",  duration: "2年", description: "两年延保服务，在标准保修基础上延长一年"});

// --- 兼容关系 (COMPATIBLE_WITH) ---
MATCH (a:Product {name: "Cam-A1"}),    (b:Product {name: "Hub-Z1"})   CREATE (a)-[:COMPATIBLE_WITH]->(b);
MATCH (a:Product {name: "Sensor-T1"}), (b:Product {name: "Hub-Z1"})   CREATE (a)-[:COMPATIBLE_WITH]->(b);
MATCH (a:Product {name: "Lock-D1"}),   (b:Product {name: "Hub-Z1"})   CREATE (a)-[:COMPATIBLE_WITH]->(b);
MATCH (a:Product {name: "Light-B1"}),  (b:Product {name: "Hub-Z1"})   CREATE (a)-[:COMPATIBLE_WITH]->(b);
MATCH (a:Product {name: "Plug-P1"}),   (b:Product {name: "Hub-Z1"})   CREATE (a)-[:COMPATIBLE_WITH]->(b);

// --- 协议支持 (SUPPORTS) ---
MATCH (p:Product {name: "Cam-A1"}),    (pr:Protocol {name: "WiFi"})      CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Cam-A1"}),    (pr:Protocol {name: "Zigbee"})    CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Hub-Z1"}),    (pr:Protocol {name: "Zigbee"})    CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Hub-Z1"}),    (pr:Protocol {name: "WiFi"})      CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Hub-Z1"}),    (pr:Protocol {name: "Thread"})    CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Sensor-T1"}), (pr:Protocol {name: "Zigbee"})    CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Lock-D1"}),   (pr:Protocol {name: "Zigbee"})    CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Lock-D1"}),   (pr:Protocol {name: "Bluetooth"}) CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Light-B1"}),  (pr:Protocol {name: "Zigbee"})    CREATE (p)-[:SUPPORTS]->(pr);
MATCH (p:Product {name: "Plug-P1"}),   (pr:Protocol {name: "WiFi"})      CREATE (p)-[:SUPPORTS]->(pr);

// --- 配件需求 (REQUIRES) ---
MATCH (p:Product {name: "Cam-A1"}), (a:Accessory {name: "Mount-K1"})  CREATE (p)-[:REQUIRES]->(a);
MATCH (p:Product {name: "Hub-Z1"}), (a:Accessory {name: "Battery-B2"}) CREATE (p)-[:REQUIRES]->(a);

// --- 保修覆盖 (COVERED_BY) ---
MATCH (p:Product {name: "Cam-A1"}),    (pol:Policy {name: "Standard-1Y"}) CREATE (p)-[:COVERED_BY]->(pol);
MATCH (p:Product {name: "Hub-Z1"}),    (pol:Policy {name: "Premium-3Y"})  CREATE (p)-[:COVERED_BY]->(pol);
MATCH (p:Product {name: "Sensor-T1"}), (pol:Policy {name: "Standard-1Y"}) CREATE (p)-[:COVERED_BY]->(pol);
MATCH (p:Product {name: "Lock-D1"}),   (pol:Policy {name: "Extended-2Y"}) CREATE (p)-[:COVERED_BY]->(pol);
MATCH (p:Product {name: "Light-B1"}),  (pol:Policy {name: "Standard-1Y"}) CREATE (p)-[:COVERED_BY]->(pol);
MATCH (p:Product {name: "Plug-P1"}),   (pol:Policy {name: "Standard-1Y"}) CREATE (p)-[:COVERED_BY]->(pol);

// --- 验证统计 ---
MATCH (n) RETURN labels(n)[0] AS node_type, count(n) AS count ORDER BY node_type;