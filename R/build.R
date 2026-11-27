# Prepare the page data from the raw Comtrade files.
#
# The page computes average prices itself (total value / total quantity), so it can
# apply the user's filters: period, countries, extreme prices, small shipments.
# For that, each product gets one small file with its monthly rows by partner.
#
# Outputs in site/data/:
#   products.json     list of products, names in English and Armenian, meta data
#   partners.json     countries: Armenian and English names, capital location
#   p/<hs6>.json      monthly rows: [month index, partner code, value USD, net kg, quantity]
#   c/<hs6>.json      yearly rows of other countries: [year, reporter, partner, value USD, net kg]
#   armenia_trade_prices.xlsx / .csv   simple averages for the last 12 and 36 months

invisible(Sys.setlocale("LC_ALL", "C.UTF-8"))
suppressPackageStartupMessages({
  library(data.table)
  library(jsonlite)
  library(openxlsx)
})

RAW <- file.path("data", "raw")
REF <- file.path("data", "ref")
OUT <- file.path("site", "data")
FLOWS <- c(X = "Export", M = "Import")
REGION <- c(268L, 31L, 792L, 364L, 398L, 860L, 417L, 112L, 498L, 804L)  # Georgia, Azerbaijan, Türkiye, Iran, Kazakhstan, Uzbekistan, Kyrgyzstan, Belarus, Moldova, Ukraine
TOP_REPORTERS <- 25

pad6 <- function(x) gsub(" ", "0", sprintf("%6s", as.character(x)))
short <- function(s, n = 160) {
  s <- trimws(gsub("\\s+", " ", ifelse(is.na(s), "", s)))
  ifelse(nchar(s) <= n, s, paste0(trimws(substr(s, 1, n - 1)), "…"))
}
js <- function(x) toJSON(x, auto_unbox = TRUE, null = "null", na = "null", digits = NA)
month_index <- function(p, first) (as.integer(substr(p, 1, 4)) - as.integer(substr(first, 1, 4))) * 12 +
  as.integer(substr(p, 5, 6)) - as.integer(substr(first, 5, 6))

# ---------- Armenia, monthly ----------
df <- rbindlist(lapply(sort(list.files(RAW, pattern = "\\.csv\\.gz$", full.names = TRUE)), fread,
                       colClasses = c(period = "character", cmdCode = "character")), fill = TRUE)
for (c in c("primaryValue", "netWgt", "qty")) set(df, j = c, value = fifelse(is.na(df[[c]]), 0, as.numeric(df[[c]])))
df <- df[partnerCode > 0 & flowCode %in% names(FLOWS) & primaryValue > 0]
df[, cmdCode := pad6(cmdCode)]
df[is.na(qtyUnitAbbr) | qtyUnitAbbr == "", qtyUnitAbbr := "N/A"]
first <- min(df$period); latest <- max(df$period)
months <- sort(unique(df$period))
df[, mi := month_index(period, first)]
df[, q := fifelse(!qtyUnitAbbr %in% c("N/A", "kg") & qty > 0, qty, 0)]
setorder(df, period)

hy <- if (file.exists(file.path(REF, "hs6_hy.json"))) fromJSON(file.path(REF, "hs6_hy.json")) else list()
desc <- df[!is.na(cmdDesc) & cmdDesc != "", .(d = last(cmdDesc)), by = cmdCode]
desc <- setNames(desc$d, desc$cmdCode)
hyname <- function(h) if (!is.null(hy[[h]])) hy[[h]] else NULL

unlink(file.path(OUT, "p"), recursive = TRUE); dir.create(file.path(OUT, "p"), recursive = TRUE)
last12 <- month_index(latest, first) - 11
units <- df[q > 0, .N, by = .(cmdCode, flowCode, qtyUnitAbbr)][order(-N), .SD[1], by = .(cmdCode, flowCode)]
units <- setNames(units$qtyUnitAbbr, paste(units$cmdCode, units$flowCode))

setorder(df, cmdCode, flowCode, mi, partnerCode)
products <- list()
for (g in split(df, by = "cmdCode", keep.by = TRUE)) {
  h <- g$cmdCode[1]
  rec <- list(hs = h, en = short(desc[h], 400), hy = hyname(h), f = list())
  v12 <- list()
  for (gf in split(g, by = "flowCode")) {
    fl <- gf$flowCode[1]
    u <- unname(units[paste(h, fl)]); if (is.na(u)) u <- NULL
    qcol <- if (is.null(u)) rep(0, nrow(gf)) else fifelse(gf$qtyUnitAbbr == u, gf$q, 0)
    m <- cbind(gf$mi, gf$partnerCode, round(gf$primaryValue), round(gf$netWgt, 1), round(qcol, 1))
    rec$f[[fl]] <- list(u = u, r = unname(m))
    v12[[fl]] <- round(sum(gf[mi >= last12, primaryValue]))
  }
  writeLines(js(rec), file.path(OUT, "p", paste0(h, ".json")), useBytes = TRUE)
  products[[length(products) + 1]] <- list(h, short(desc[h]), hyname(h), I(sort(names(rec$f))), v12)
}

meta <- list(first = first, latest = latest, months = months, built = format(Sys.Date()),
             n_products = length(products), names_hy = length(hy) > 0)
writeLines(js(list(meta = meta, products = products)), file.path(OUT, "products.json"), useBytes = TRUE)
file.copy(file.path(REF, "partners.json"), file.path(OUT, "partners.json"), overwrite = TRUE)

# Simple download: average price over the last 12 and 36 months, all partners together
lab <- c(`12` = "Last 12 months", `36` = "Last 36 months")
dl <- rbindlist(lapply(c(12, 36), function(n) {
  a <- df[mi >= month_index(latest, first) - (n - 1), .(v = sum(primaryValue), kg = sum(netWgt)), by = .(cmdCode, flowCode)]
  a[, .(HS6 = cmdCode, `Ապրանք` = vapply(cmdCode, function(h) if (is.null(hy[[h]])) "" else hy[[h]], ""),
        Product = unname(desc[cmdCode]), Flow = FLOWS[flowCode], Period = lab[[as.character(n)]],
        `Value (USD)` = round(v), `Net weight (kg)` = round(kg),
        `Average price (USD per kg)` = fifelse(kg > 0, round(v / kg, 4), NA_real_))]
}))
fwrite(dl, file.path(OUT, "armenia_trade_prices.csv"), bom = TRUE)
wb <- createWorkbook()
addWorksheet(wb, "About")
writeData(wb, "About", data.frame(About = c(
  "Average export and import prices of Armenia by 6-digit HS product.",
  "Source: UN Comtrade, monthly data reported by Armenia.",
  "Average price = total value / total net weight over the period, all partner countries together, no filters.",
  sprintf("Data %s-%s to %s-%s. Built %s with R.", substr(first, 1, 4), substr(first, 5, 6), substr(latest, 1, 4), substr(latest, 5, 6), meta$built))))
for (n in c("12", "36")) { addWorksheet(wb, lab[[n]]); writeData(wb, lab[[n]], dl[Period == lab[[n]]]) }
saveWorkbook(wb, file.path(OUT, "armenia_trade_prices.xlsx"), overwrite = TRUE)
message(sprintf("Built %d products, %s to %s", length(products), first, latest))

# ---------- other countries, yearly (Compare view) ----------
unlink(file.path(OUT, "c"), recursive = TRUE)
cf <- list.files(file.path("data", "compare"), pattern = "^[XM]_\\d{4}\\.csv\\.gz$", full.names = TRUE)
n_cmp <- 0
if (length(cf)) {
  dir.create(file.path(OUT, "c"), recursive = TRUE)
  cd <- rbindlist(lapply(cf, fread, colClasses = c(cmdCode = "character"), encoding = "UTF-8"), fill = TRUE)
  for (c in c("primaryValue", "netWgt")) set(cd, j = c, value = fifelse(is.na(cd[[c]]), 0, as.numeric(cd[[c]])))
  cd <- cd[primaryValue > 0 & reporterCode > 0]
  cd[, `:=`(year = as.integer(substr(as.character(period), 1, 4)), cmdCode = pad6(cmdCode))]
  setorder(cd, cmdCode, flowCode, year, reporterCode, partnerCode)
  for (g in split(cd, by = "cmdCode")) {
    h <- g$cmdCode[1]
    rec <- list(hs = h, years = I(sort(unique(g$year))), f = list())
    for (gf in split(g, by = "flowCode")) {
      w <- gf[partnerCode == 0, .(v = sum(primaryValue)), by = reporterCode][order(-v)]
      keep <- unique(c(head(w$reporterCode, TOP_REPORTERS), REGION, 51L))
      gf <- gf[reporterCode %in% keep]
      rec$f[[gf$flowCode[1]]] <- unname(cbind(gf$year, gf$reporterCode, gf$partnerCode, round(gf$primaryValue), round(gf$netWgt)))
    }
    writeLines(js(rec), file.path(OUT, "c", paste0(h, ".json")), useBytes = TRUE)
    n_cmp <- n_cmp + 1
  }
}
message("Compare files: ", n_cmp)
