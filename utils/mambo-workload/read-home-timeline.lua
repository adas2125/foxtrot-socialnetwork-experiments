-- CLUSTER CHANGE when used with wrk2: requests populate caches and traces.
local next_thread = 0
function setup(thread)
  thread:set("thread_id", next_thread)
  next_thread = next_thread + 1
end

local base, readers, posts, page
function init(args)
  base = assert(tonumber(os.getenv("READER_BASE")))
  readers = assert(tonumber(os.getenv("READERS")))
  posts = assert(tonumber(os.getenv("POSTS")))
  page = tonumber(os.getenv("PAGE_SIZE")) or 10
  assert(readers > 0 and posts >= page and page > 0)
  math.randomseed((tonumber(os.getenv("SEED")) or 42) + thread_id)
  math.random(); math.random(); math.random()
end

function request()
  local reader = string.format("%.0f", base + math.random(0, readers - 1))
  local start = math.random(0, posts - page)
  return wrk.format("GET", "/wrk2-api/home-timeline/read?user_id=" .. reader ..
    "&start=" .. start .. "&stop=" .. (start + page))
end
