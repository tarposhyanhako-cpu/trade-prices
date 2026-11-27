# Download yearly trade of ALL reporting countries for the products Armenia trades,
# to compare Armenia's prices with the main exporters/importers and the region.
#
# For each year and flow, Comtrade is asked for many 6-digit products at once,
# all reporters, and a short list of partners: World plus Armenia's 20 main
# partners. The free key allows about 500 calls a day, so the job stops at
# MAX_CALLS and continues on the next run. State is kept in data/compare/.

invisible(Sys.setlocale("LC_ALL", "C.UTF-8"))
suppressPackageStartupMessages({
  library(httr2)
  library(jsonlite)
  library(data.table)
})

RAW <- file.path("data", "raw")
OUT <- file.path("data", "compare")
BASE <- "https://comtradeapi.un.org/data/v1/get/C/A/HS"
MAX_CALLS <- as.integer(Sys.getenv("MAX_CALLS", "440"))
BATCH <- as.integer(Sys.getenv("BATCH", "60"))
YEARS <- as.integer(Sys.getenv("YEARS", "5"))
SLEEP <- 1.3
MAX_RECORDS <- 100000
KEEP <- c("period", "flowCode", "reporterCode", "partnerCode", "cmdCode", "primaryValue", "netWgt")

KEY <- trimws(Sys.getenv("COMTRADE_API_KEY"))
if (KEY == "") stop("COMTRADE_API_KEY is not set")
calls <- 0L
errors <- list()

comtrade_get <- function(params, tries = 5) {
  for (t in seq_len(tries)) {
    if (calls >= MAX_CALLS) stop(structure(class = c("limit", "condition"), list(message = "limit")))
    Sys.sleep(SLEEP)
    calls <<- calls + 1L
    resp <- request(BASE) |> req_url_query(!!!params) |>
      req_headers(`Ocp-Apim-Subscription-Key` = KEY) |> req_timeout(240) |>
      req_error(is_error = function(r) FALSE) |> req_perform()
    st <- resp_status(resp)
    if (st == 200) return(fromJSON(resp_body_string(resp), simplifyDataFrame = TRUE))
    errors[[length(errors) + 1]] <<- list(status = st, text = substr(resp_body_string(resp), 1, 300))
    if (st %in% c(429, 500, 502, 503, 504)) { Sys.sleep(25 * t); next }
    stop(sprintf("HTTP %d: %s", st, substr(resp_body_string(resp), 1, 300)))
  }
  stop("too many failed tries")
}

dir.create(OUT, recursive = TRUE, showWarnings = FALSE)
arm <- rbindlist(lapply(list.files(RAW, pattern = "\\.csv\\.gz$", full.names = TRUE), fread,
                        select = c("period", "flowCode", "partnerCode", "cmdCode", "primaryValue"),
                        colClasses = c(period = "character", cmdCode = "character")))
arm[, cmdCode := sprintf("%06s", cmdCode)][, cmdCode := gsub(" ", "0", cmdCode)]
latest_year <- as.integer(substr(max(arm$period), 1, 4))
years <- (latest_year - YEARS):(latest_year - 1)          # complete years only
recent <- arm[period >= paste0(years[1], "01") & partnerCode > 0]
partners <- c(0L, as.integer(recent[, .(v = sum(primaryValue)), by = partnerCode][order(-v)][1:20, partnerCode]))
products <- sort(unique(arm$cmdCode))

state_f <- file.path(OUT, "state.json")
state <- if (file.exists(state_f)) fromJSON(state_f) else list()
if (!identical(as.integer(state$partners), partners) || !identical(as.integer(state$years), years))
  state <- list(partners = partners, years = years, done = character())
done <- as.character(state$done)

jobs <- CJ(y = sort(years, decreasing = TRUE), fl = c("X", "M"), i = seq(0, length(products) - 1, by = BATCH), sorted = FALSE)
jobs[, id := sprintf("%d-%s-%d", y, fl, i)]
todo <- jobs[!id %in% done]
message(sprintf("Partners: %s\nYears: %s\nJobs left: %d of %d",
                paste(partners, collapse = ","), paste(years, collapse = ","), nrow(todo), nrow(jobs)))

fetch_batch <- function(y, fl, codes) {
  js <- comtrade_get(list(period = y, flowCode = fl, cmdCode = paste(codes, collapse = ","),
                          partnerCode = paste(partners, collapse = ","), partner2Code = 0,
                          customsCode = "C00", motCode = 0, maxRecords = MAX_RECORDS))
  d <- as.data.table(js$data)
  if (nrow(d) >= MAX_RECORDS && length(codes) > 1) {          # too many rows: split the batch
    h <- length(codes) %/% 2
    return(rbind(fetch_batch(y, fl, codes[1:h]), fetch_batch(y, fl, codes[(h + 1):length(codes)]), fill = TRUE))
  }
  if (!nrow(d)) return(NULL)
  for (c in setdiff(KEEP, names(d))) d[[c]] <- NA
  d[, ..KEEP]
}

new <- list()
tryCatch({
  for (k in seq_len(nrow(todo))) {
    j <- todo[k]
    codes <- products[(j$i + 1):min(j$i + BATCH, length(products))]
    d <- fetch_batch(j$y, j$fl, codes)
    key <- paste(j$fl, j$y, sep = "_")
    if (!is.null(d)) new[[key]] <- rbind(new[[key]], d, fill = TRUE)
    done <- c(done, j$id)
    message(sprintf("  %d %s batch %d: %d rows (calls %d)", j$y, j$fl, j$i %/% BATCH + 1, if (is.null(d)) 0 else nrow(d), calls))
  }
}, limit = function(e) message(sprintf("Reached the daily call limit (%d); will continue next run", MAX_CALLS)),
   error = function(e) { errors[[length(errors) + 1]] <<- list(error = conditionMessage(e)); message("Stopped: ", conditionMessage(e)) })

for (key in names(new)) {
  f <- file.path(OUT, paste0(key, ".csv.gz"))
  add <- new[[key]]
  add[, cmdCode := gsub(" ", "0", sprintf("%06s", as.character(cmdCode)))]
  if (file.exists(f)) add <- rbind(fread(f, colClasses = c(cmdCode = "character")), add, fill = TRUE)
  add <- unique(add, by = c("period", "flowCode", "reporterCode", "partnerCode", "cmdCode"), fromLast = TRUE)
  fwrite(add, f, compress = "gzip")
}
state$done <- sort(unique(done)); state$complete <- length(state$done) >= nrow(jobs)
write_json(state, state_f, auto_unbox = TRUE)
write_json(list(calls = calls, jobs_total = nrow(jobs), jobs_done = length(state$done), errors = tail(errors, 20)),
           file.path(OUT, "log.json"), auto_unbox = TRUE, pretty = TRUE)
message(sprintf("Done for now: %d/%d jobs, %d calls", length(state$done), nrow(jobs), calls))
