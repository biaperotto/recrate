# THROWAWAY SPIKE — pinned to Jones et al. (2026), not general reCRATE code.
#
# Regenerates the paper's swimmer plot by running the authors' own plotting code
# at their own pinned R version, from their own shared spreadsheet.
#
# Lifted verbatim (apart from paths) from the authors' Quarto source:
#   "Challenges in the Computational Reproducibility of Linear Regression
#    Analyses An Empirical Study Sept 6.qmd", lines 213 and 1291-1395,
#   github.com/Lee-V-Jones/Reproducibility @ 370e615
#
# Usage: Rscript swimmer.R <repo-dir> <out-dir>

args <- commandArgs(trailingOnly = TRUE)
repo <- if (length(args) >= 1) args[1] else "/repo"
out  <- if (length(args) >= 2) args[2] else "/out"

suppressPackageStartupMessages({
  library(readxl)
  library(dplyr)
  library(ggplot2)
  library(forcats)
  library(grid)
})

# What the paper's own Inferential_session_info.txt records, against what we got.
# Reported honestly rather than hidden: the rocker snapshot for R 4.4.2 predates
# some of these, and the figure reproduces anyway.
expected <- c(R = "4.4.2", ggplot2 = "4.0.1", dplyr = "1.1.4", readxl = "1.4.5")
actual <- c(R = as.character(getRversion()),
            ggplot2 = as.character(packageVersion("ggplot2")),
            dplyr   = as.character(packageVersion("dplyr")),
            readxl  = as.character(packageVersion("readxl")))

cat("Versions (paper's session info vs this container)\n")
for (p in names(expected)) {
  cat(sprintf("  %-8s session info %-8s container %-8s %s\n",
              p, expected[[p]], actual[[p]],
              if (identical(expected[[p]], actual[[p]])) "match" else "DIFFERS"))
}
cat("\n")

src <- file.path(repo, "data", "model_data_long_with_dictionary.xlsx")
cat("Reading the authors' shared data:", src, "\n")
model_data <- read_excel(src, sheet = "model_data_long")
cat("Rows read:", nrow(model_data), "\n\n")

# ---- the authors' code, unchanged ------------------------------------------

swimmerdatB <- model_data %>%
  dplyr::select(Model, term, result_O_est_category, paper_ID) %>%
  rename(Status = result_O_est_category, Paper = paper_ID) %>%
  na.omit() %>%
  mutate(Paper = as.factor(Paper)) %>%
  mutate(Model = case_when(
    Model == 1 ~ "Model 1",
    Model == 2 ~ "Model 2",
    Model == 3 ~ "Model 3",
    TRUE ~ as.character(Model)
  )) %>%
  group_by(Paper) %>% mutate(Estimate = 1:n()) %>% ungroup() %>%
  mutate(Status = factor(Status,
    levels = c("Reproduced", "Incorrect Rounding", "Not Reproduced")))

swimmerdatBSTD <- model_data %>%
  dplyr::select(Model, term, result_O_stdEst_category, paper_ID) %>%
  rename(Status = result_O_stdEst_category, Paper = paper_ID) %>%
  na.omit() %>%
  mutate(Paper = as.factor(Paper)) %>%
  mutate(Model = case_when(
    Model == 1 ~ "Model 1",
    Model == 2 ~ "Model 2",
    Model == 3 ~ "Model 3",
    TRUE ~ as.character(Model)
  )) %>%
  group_by(Paper) %>% mutate(Estimate = 1:n()) %>% ungroup() %>%
  mutate(Status = factor(Status,
    levels = c("Reproduced", "Incorrect Rounding", "Not Reproduced")))

swimmerdatB <- rbind(swimmerdatB, swimmerdatBSTD)
swimmerdatB <- swimmerdatB %>%
  mutate(Paper = forcats::fct_inseq(Paper)) %>%
  arrange(Paper, Estimate)

swimmerB <- ggplot(swimmerdatB, aes(x = Estimate, y = Paper)) +
  scale_y_discrete(limits = rev) +
  scale_x_continuous(expand = expansion(add = c(0.5, 0.5))) +
  geom_tile(aes(fill = Model), height = 0.9) +
  geom_point(aes(shape = Status, color = Status),
             size = 5, fill = NA, show.legend = TRUE) +
  scale_fill_manual(
    values = c("Model 1" = "#A6CEE3", "Model 2" = "#B2DF8A", "Model 3" = "#FDBF6F"),
    name = "Model") +
  scale_color_manual(
    values = c("Reproduced" = "black", "Incorrect Rounding" = "black",
               "Not Reproduced" = "black"), name = "Status") +
  scale_shape_manual(
    values = c("Reproduced" = 16, "Incorrect Rounding" = 1, "Not Reproduced" = 4),
    name = "Status") +
  labs(title = "", x = "Regression Coefficient Number", y = " Paper",
       fill = "Model", color = "Status", shape = "Status") +
  theme_bw() +
  theme(
    panel.grid.major.y = element_blank(),
    axis.text.y = element_text(size = 16),
    axis.text.x = element_text(size = 16),
    axis.title = element_text(size = 16),
    plot.title = element_text(size = 16, face = "bold"),
    legend.title = element_text(size = 12),
    legend.text = element_text(size = 12)) +
  guides(
    fill  = guide_legend(override.aes = list(shape = NA)),
    color = guide_legend(override.aes = list(fill = NA)),
    shape = guide_legend(override.aes = list(fill = NA)))

swimmerB <- swimmerB +
  annotate("label", x = 12.5, y = "22",
           label = "For paper 22,\n each model had \n one coefficient that \n was not reproduced",
           hjust = 0, size = 4.5, label.size = 0.3, fill = "white")

swimmerB <- swimmerB +
  annotate("segment", x = 12.3, xend = 4.2, y = "22", yend = "22",
           arrow = arrow(length = unit(0.25, "cm")), linewidth = 0.6)

# ---- end of the authors' code ----------------------------------------------

dir.create(out, showWarnings = FALSE, recursive = TRUE)
dest <- file.path(out, "swimmerB.regenerated.png")
ggsave(filename = dest, plot = swimmerB, width = 10, height = 8, dpi = 150)

cat("\nRegenerated figure written to:", dest, "\n")
cat("Rows plotted:", nrow(swimmerdatB), "\n")
cat("Status tally:\n")
print(table(swimmerdatB$Status, useNA = "ifany"))
