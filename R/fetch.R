# Download Armenia's monthly trade by 6-digit HS product and partner from UN Comtrade.
#
# Stores one compressed CSV per year in data/raw/. Months already stored are
# skipped, except the most recent REFRESH_MONTHS, which are downloaded again
# because countries revise recent data.
#
# Needs the environment variable COMTRADE_API_KEY (free key from comtrade.un.org).

invisible(Sys.setlocale("LC_ALL", "C.UTF-8"))
suppressPackageStartupMessages({
  library(httr2)
  library(jsonlite)
  library(data.table)
})

REPORTER <- 51          # Armenia (M49 code)
START <- "201601"       # first month to collect
REFRESH_MONTHS <- 6     # re-download the latest months on every run
SLEEP <- 1.2            # seconds between calls (free key: about 1 call per second)
MAX_RECORDS <- 100000   # largest answer allowed for a free key
BASE <- "https://comtradeapi.un.org/data/v1"
RAW <- file.path("data", "raw")
KEEP <- c("period", "flowCode", "partnerCode", "partnerDesc", "cmdCode", "cmdDesc",
          "primaryValue", "netWgt", "qty", "qtyUnitAbbr", "isQtyEstimated", "isNetWgtEstimated")

KEY <- trimws(Sys.getenv("COMTRADE_API_KEY"))
if (KEY == "") stop("COMTRADE_API_KEY is not set")
calls <- 0L

comtrade_get <- function(path, params, tries = 6) {
  for (t in seq_len(tries)) {
    Sys.sleep(SLEEP)
    calls <<- calls + 1L
    resp <- request(paste0(BASE, path)) |>
      req_url_query(!!!params) |>
      req_headers(`Ocp-Apim-Subscription-Key` = KEY) |>
      req_timeout(180) |>
      req_error(is_error = function(r) FALSE) |>
      req_perform()
    st <- resp_status(resp)
    if (st == 200) return(fromJSON(resp_body_string(resp), simplifyDataFrame = TRUE))
    if (st %in% c(429, 500, 502, 503, 504)) {
      message(sprintf("  HTTP %d, waiting %ds", st, 20 * t)); Sys.sleep(20 * t); next
    }
    stop(sprintf("HTTP %d: %s", st, substr(resp_body_string(resp), 1, 300)))
  }
  stop("too many failed tries")
}

month_seq <- function(from, to) {
  k0 <- as.integer(substr(from, 1, 4)) * 12 + as.integer(substr(from, 5, 6)) - 1
  k1 <- as.integer(substr(to, 1, 4)) * 12 + as.integer(substr(to, 5, 6)) - 1
  k <- k0:k1
  sprintf("%d%02d", k %/% 12, k %% 12 + 1)
}

# Months for which Armenia's monthly HS data exists in Comtrade
available_months <- function() {
  js <- tryCatch(comtrade_get("/getDA/C/M/HS", list(reporterCode = REPORTER)), error = function(e) NULL)
  if (!is.null(js) && length(js$data)) {
    m <- sort(unique(as.character(js$data$period)))
    m <- m[m >= START]
    if (length(m)) return(m)
  }
  message("Availability check failed; trying every month instead")
  d <- seq(Sys.Date(), by = "-2 months", length.out = 2)[2]
  month_seq(START, format(d, "%Y%m"))
}

fetch_month <- function(period, flow) {
  js <- comtrade_get("/get/C/M/HS", list(reporterCode = REPORTER, period = period, flowCode = flow,
                                          cmdCode = "AG6", partner2Code = 0, customsCode = "C00", motCode = 0,
                                          includeDesc = "true", maxRecords = MAX_RECORDS))
  d <- as.data.table(js$data)
  if (!nrow(d)) return(NULL)
  if (nrow(d) >= MAX_RECORDS) message(sprintf("  WARNING %s %s: hit %d rows, answer may be cut", period, flow, MAX_RECORDS))
  for (c in setdiff(KEEP, names(d))) d[[c]] <- NA
  d[, ..KEEP]
}

dir.create(RAW, recursive = TRUE, showWarnings = FALSE)
months <- available_months()
message(sprintf("Comtrade has %d monthly periods for Armenia since %s: %s to %s",
                length(months), START, months[1], tail(months, 1)))

files <- list.files(RAW, pattern = "^\\d{4}\\.csv\\.gz$", full.names = TRUE)
have <- unique(unlist(lapply(files, function(f) as.character(fread(f, select = "period", colClasses = "character")$period))))
todo <- months[!(months %in% have) | months %in% tail(months, REFRESH_MONTHS)]
message(sprintf("Months to download: %d (about %d calls)", length(todo), 2 * length(todo)))

new <- list()
for (m in todo) {
  parts <- lapply(c("X", "M"), function(fl) { d <- fetch_month(m, fl); message(sprintf("  %s %s: %d rows", m, fl, if (is.null(d)) 0 else nrow(d))); d })
  y <- substr(m, 1, 4)
  new[[y]] <- rbind(new[[y]], rbindlist(parts, fill = TRUE))
}

for (y in names(new)) {
  f <- file.path(RAW, paste0(y, ".csv.gz"))
  add <- new[[y]]
  add[, period := as.character(period)]
  if (file.exists(f)) {
    old <- fread(f, colClasses = c(period = "character", cmdCode = "character"), encoding = "UTF-8")
    old <- old[!period %in% unique(add$period)]
    add <- rbindlist(list(old, add), fill = TRUE)
  }
  setorder(add, period, flowCode, cmdCode, partnerCode)
  fwrite(add, f, compress = "gzip")
  message(sprintf("Saved %s: %d rows", basename(f), nrow(add)))
}

write_json(list(months_available = months, downloaded = todo, api_calls = calls),
           file.path("data", "fetch_log.json"), auto_unbox = TRUE, pretty = TRUE)
message(sprintf("Done. API calls used: %d", calls))
