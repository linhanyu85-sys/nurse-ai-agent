options(stringsAsFactors = FALSE)

# ===========================
# 0. 路径设置
# ===========================
work_dir <- "D:/Desktop/chl论文"
data_path <- file.path(work_dir, "经产妇（网络分析用）.csv")
out_dir <- "D:/网络分析结果经产妇chl"

setwd(work_dir)

if (!dir.exists(out_dir)) {
  dir.create(out_dir, recursive = TRUE)
}

# ===========================
# 1. 安装并加载包
# ===========================
pkg_needed <- c(
  "bootnet", "qgraph", "networktools", "ggplot2",
  "magrittr", "dplyr"
)

pkg_installed <- rownames(installed.packages())
for (p in pkg_needed) {
  if (!(p %in% pkg_installed)) {
    install.packages(p, dependencies = TRUE)
  }
}

suppressPackageStartupMessages({
  library(bootnet)
  library(qgraph)
  library(networktools)
  library(ggplot2)
  library(magrittr)
  library(dplyr)
})

# ===========================
# 2. 通用保存函数
# 关键修复：
# 1) bootnet / networktools 现在很多图返回 ggplot 对象
# 2) 在脚本里导出到 png 时，ggplot 必须显式 print()
# 3) qgraph 这类 base plot 则会在执行表达式时直接画到设备上
# ===========================
save_plot_png <- function(filename,
                          plot_expr,
                          width = 3000,
                          height = 2400,
                          res = 300,
                          bg = "white") {
  grDevices::png(
    filename = filename,
    width = width,
    height = height,
    res = res,
    bg = bg
  )
  on.exit(grDevices::dev.off(), add = TRUE)

  result <- tryCatch(
    force(plot_expr),
    error = function(e) {
      message("绘图失败: ", filename)
      message("错误信息: ", conditionMessage(e))
      return(NULL)
    }
  )

  if (inherits(result, "ggplot")) {
    print(result)
  } else if (inherits(result, c("grob", "gTree", "gtable"))) {
    grid::grid.draw(result)
  }

  invisible(result)
}

# ===========================
# 3. 读取数据
# ===========================
myData <- read.csv(
  data_path,
  header = TRUE,
  stringsAsFactors = FALSE,
  fileEncoding = "UTF-8-BOM"
)

cat("原始样本量：", nrow(myData), "\n")

# 取 A:Z 共 26 列，但只使用 PHQ 和 GAD（去掉 PSS 的 10 列）
myData <- myData[, 11:26]

colnames(myData) <- c(
  paste0("PHQ", 1:9),
  paste0("GAD", 1:7)
)

myData[] <- lapply(myData, function(x) as.numeric(as.character(x)))

# 删除包含缺失值的被试（listwise deletion）
myData <- na.omit(myData)

cat("处理后有效样本量：", nrow(myData), "\n")

# ===========================
# 4. 描述检查
# ===========================
check_table <- data.frame(
  Node = names(myData),
  Mean = sapply(myData, mean),
  SD = sapply(myData, sd),
  Min = sapply(myData, min),
  Max = sapply(myData, max),
  UniqueN = sapply(myData, function(x) length(unique(x)))
)

write.csv(
  check_table,
  file.path(out_dir, "节点分布检查.csv"),
  row.names = FALSE
)

# ===========================
# 5. 标签和分组
# ===========================
phq_labels <- c(
  "PHQ1 Little interest or pleasure",
  "PHQ2 Feeling down/depressed/hopeless",
  "PHQ3 Trouble sleeping",
  "PHQ4 Feeling tired or little energy",
  "PHQ5 Poor appetite or overeating",
  "PHQ6 Feeling bad about yourself",
  "PHQ7 Trouble concentrating",
  "PHQ8 Moving/speaking slowly or restless",
  "PHQ9 Thoughts of self-harm"
)

gad_labels <- c(
  "GAD1 Feeling nervous/anxious/on edge",
  "GAD2 Unable to stop/control worrying",
  "GAD3 Worrying too much",
  "GAD4 Trouble relaxing",
  "GAD5 Restless/hard to sit still",
  "GAD6 Easily annoyed or irritable",
  "GAD7 Afraid something awful may happen"
)

symptom_labels <- c(phq_labels, gad_labels)

groups_list <- list(
  "PHQ-9" = 1:9,
  "GAD-7" = 10:16
)

groups_bridge <- c(
  rep("PHQ-9", 9),
  rep("GAD-7", 7)
)

# ===========================
# 6. 主网络估计
# ===========================
Network <- estimateNetwork(
  myData,
  default = "EBICglasso",
  corMethod = "cor_auto",
  corArgs = list(forcePD = TRUE),
  tuning = 0.5
)

# ===========================
# 7. 网络图
# ===========================
save_plot_png(
  file.path(out_dir, "网络图_变量名.png"),
  plot(
    Network,
    layout = "spring",
    groups = groups_list,
    nodeNames = colnames(myData),
    label.cex = 0.8,
    label.color = "black",
    negDashed = TRUE,
    legend = TRUE,
    legend.cex = 0.45,
    legend.mode = "style2",
    maximum = 0.45,
    minimum = 0.03
  ),
  width = 3200,
  height = 3200
)

save_plot_png(
  file.path(out_dir, "网络图_英文标签.png"),
  plot(
    Network,
    layout = "spring",
    groups = groups_list,
    nodeNames = symptom_labels,
    label.cex = 0.55,
    label.color = "black",
    negDashed = TRUE,
    legend = TRUE,
    legend.cex = 0.35,
    legend.mode = "style2",
    maximum = 0.45,
    minimum = 0.03
  ),
  width = 3200,
  height = 3200
)

g <- save_plot_png(
  file.path(out_dir, "网络图_正式版.png"),
  plot(
    Network,
    layout = "spring",
    groups = groups_list,
    nodeNames = colnames(myData),
    label.cex = 0.95,
    label.color = "black",
    negDashed = TRUE,
    legend = FALSE,
    maximum = 0.45,
    minimum = 0.03,
    color = c("#56B4E9", "#009E73")
  ),
  width = 3200,
  height = 3200
)

if (is.null(g)) {
  g <- qgraph(
    getWmat(Network),
    layout = "spring",
    groups = groups_list,
    labels = colnames(myData),
    label.cex = 0.95,
    label.color = "black",
    negDashed = TRUE,
    legend = FALSE,
    maximum = 0.45,
    minimum = 0.03,
    color = c("#56B4E9", "#009E73")
  )
}

# ===========================
# 8. 中心性分析
# ===========================
save_plot_png(
  file.path(out_dir, "中心性图_Strength_Closeness_Betweenness.png"),
  centralityPlot(
    Network,
    include = c("Strength", "Closeness", "Betweenness")
  ),
  width = 2800,
  height = 2200
)

save_plot_png(
  file.path(out_dir, "中心性图_ExpectedInfluence.png"),
  centralityPlot(
    Network,
    include = c("ExpectedInfluence")
  ),
  width = 2400,
  height = 2200
)

cent <- centrality_auto(getWmat(Network))

centrality_table <- data.frame(
  Node = rownames(cent$node.centrality),
  Strength = cent$node.centrality$Strength,
  Closeness = cent$node.centrality$Closeness,
  Betweenness = cent$node.centrality$Betweenness,
  ExpectedInfluence = cent$node.centrality$ExpectedInfluence
)

write.csv(
  centrality_table,
  file.path(out_dir, "标准中心性结果.csv"),
  row.names = FALSE
)

# ===========================
# 9. 桥梁中心性
# 注意：plot.bridge() 返回 ggplot，必须显式 print
# ===========================
b <- bridge(
  getWmat(Network),
  communities = groups_bridge,
  directed = FALSE
)

bridge_result <- data.frame(
  Node = names(b[["Bridge Strength"]]),
  Bridge_Strength = as.numeric(b[["Bridge Strength"]]),
  Bridge_EI_1step = as.numeric(b[["Bridge Expected Influence (1-step)"]])
)

write.csv(
  bridge_result,
  file.path(out_dir, "桥梁中心性结果.csv"),
  row.names = FALSE
)

save_plot_png(
  file.path(out_dir, "桥梁中心性图.png"),
  plot(
    b,
    include = c("Bridge Expected Influence (1-step)", "Bridge Strength"),
    theme_bw = FALSE,
    raw0 = TRUE,
    signed = TRUE
  ),
  width = 2800,
  height = 2200
)

# ===========================
# 10. 非参数 bootstrap
# 关键修复：
# bootnet 的 plot() 返回 ggplot，脚本模式下必须 print
# 同时把 expectedInfluence 一并存进去，后续可直接出图
# ===========================
n_cores <- max(1, parallel::detectCores() - 1)

boot1 <- bootnet(
  Network,
  nBoots = 5000,
  nCores = n_cores,
  statistics = c("edge", "strength", "expectedInfluence")
)

save_plot_png(
  file.path(out_dir, "精确性分析_bootstrap总图.png"),
  plot(boot1, labels = FALSE, order = "sample"),
  width = 2800,
  height = 2200
)

save_plot_png(
  file.path(out_dir, "边差异性检验图.png"),
  plot(boot1, "edge", plot = "difference", onlyNonZero = TRUE, order = "sample"),
  width = 3400,
  height = 2600
)

save_plot_png(
  file.path(out_dir, "节点Strength差异性检验图.png"),
  plot(boot1, "strength"),
  width = 3400,
  height = 2600
)

save_plot_png(
  file.path(out_dir, "节点ExpectedInfluence差异性检验图.png"),
  plot(boot1, "expectedInfluence"),
  width = 3400,
  height = 2600
)

result_edge <- summary(boot1) %>%
  ungroup() %>%
  filter(type == "edge") %>%
  arrange(-sample)

write.csv(
  result_edge,
  file.path(out_dir, "非参数bootstrap_边结果.csv"),
  row.names = FALSE
)

# ===========================
# 11. 稳定性分析：Case-drop bootstrap
# 关键修复：
# 默认 statistics 不包含 expectedInfluence
# 如果后面要算 EI 的 CS 系数，这里必须提前存进去
# ===========================
boot2_case <- bootnet(
  Network,
  nBoots = 5000,
  type = "case",
  nCores = n_cores,
  statistics = c("strength", "expectedInfluence")
)

cat("\n正在生成稳定性分析图...\n")

save_plot_png(
  file.path(out_dir, "稳定性分析_CaseDrop.png"),
  plot(boot2_case),
  width = 3600,
  height = 2400
)

save_plot_png(
  file.path(out_dir, "稳定性分析_Strength.png"),
  plot(boot2_case, statistics = "strength"),
  width = 3600,
  height = 2400
)

save_plot_png(
  file.path(out_dir, "稳定性分析_ExpectedInfluence.png"),
  plot(boot2_case, statistics = "expectedInfluence"),
  width = 3600,
  height = 2400
)

save_plot_png(
  file.path(out_dir, "稳定性分析_Strength和ExpectedInfluence.png"),
  plot(boot2_case, statistics = c("strength", "expectedInfluence")),
  width = 3600,
  height = 2400
)

cat("稳定性分析图已生成\n")

CS_strength <- corStability(boot2_case, statistics = "strength")
CS_expectedInf_case <- corStability(boot2_case, statistics = "expectedInfluence")

cat("Strength CS系数:", CS_strength, "\n")
cat("ExpectedInfluence CS系数:", CS_expectedInf_case, "\n")

cs_table <- data.frame(
  Statistic = c("Strength", "Expected Influence"),
  CS_coefficient = c(as.numeric(CS_strength), as.numeric(CS_expectedInf_case))
)

cat("\nCS系数结果:\n")
print(cs_table)

write.csv(
  cs_table,
  file.path(out_dir, "CS系数结果.csv"),
  row.names = FALSE
)

# ===========================
# 12. 保存对象
# ===========================
save(
  myData,
  Network,
  g,
  cent,
  centrality_table,
  b,
  bridge_result,
  boot1,
  boot2_case,
  CS_strength,
  CS_expectedInf_case,
  cs_table,
  file = file.path(out_dir, "网络分析全部结果.RData")
)

cat("====================================\n")
cat("网络分析完成！\n")
cat("当前模型为两个量表总网络：PHQ-9 + GAD-7\n")
cat("有效样本量：", nrow(myData), "\n")
cat("网络估计方法：EBICglasso (gamma = 0.5)\n")
cat("非参数 bootstrap 次数：5000（用于精确性分析）\n")
cat("Case-drop bootstrap 次数：5000（用于稳定性分析）\n")
cat("稳定性分析指标：Strength + Expected Influence\n")
cat("结果已保存到：", out_dir, "\n")
cat("本修正版已修复 ggplot 型图片导出为空白的问题。\n")
cat("====================================\n")
