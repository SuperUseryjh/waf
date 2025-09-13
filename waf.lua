local redis = require "resty.redis"

local function get_ip_blacklist(red)
    local ips, err = red:smembers("waf:ip_blacklist")
    if not ips then
        ngx.log(ngx.ERR, "failed to get ip blacklist from redis: ", err)
        return {}
    end
    local blacklist = {}
    for _, ip in ipairs(ips) do
        blacklist[ip] = true
    end
    return blacklist
end

local function get_sql_patterns(red)
    local patterns, err = red:smembers("waf:sql_patterns")
    if not patterns then
        ngx.log(ngx.ERR, "failed to get sql patterns from redis: ", err)
        return {}
    end
    return patterns
end

local function get_xss_patterns(red)
    local patterns, err = red:smembers("waf:xss_patterns")
    if not patterns then
        ngx.log(ngx.ERR, "failed to get xss patterns from redis: ", err)
        return {}
    end
    return patterns
end

local function get_waf_mode(red)
    local mode, err = red:get("waf:mode")
    if not mode then
        ngx.log(ngx.WARN, "failed to get waf mode from redis, defaulting to block: ", err)
        return "block" -- Default to block if not set
    end
    return mode
end

local sql_patterns = {
    "union select",
    "select .* from",
    "information_schema",
    "sleep(",
    "benchmark(",
}

local xss_patterns = {
    "<script>",
    "javascript:",
    "onerror=",
    "onload=",
}

local function get_redis_client()
    local red = redis:new()
    red:set_timeout(1000) -- 1 second

    -- Connect to Redis service (defined in docker-compose.yml)
    local ok, err = red:connect("redis", 6379)
    if not ok then
        ngx.log(ngx.ERR, "failed to connect to redis: ", err)
        return nil
    end
    return red
end

local function log_access_data()
    local red = get_redis_client()
    if not red then
        return
    end

    local remote_addr = ngx.var.remote_addr

    -- Log unique visitors (IPs)
    local ok, err = red:sadd("unique_ips", remote_addr)
    if not ok then
        ngx.log(ngx.ERR, "failed to add IP to unique_ips: ", err)
    end

    -- Log total requests for the last 24 hours
    local total_requests_key = "total_requests_24h"
    ok, err = red:incr(total_requests_key)
    if not ok then
        ngx.log(ngx.ERR, "failed to increment total_requests: ", err)
    else
        -- Set expiration for 24 hours (86400 seconds) if it's a new key
        red:expire(total_requests_key, 86400)
    end

    red:close()
end

-- Timer function to snapshot historical data
local function snapshot_history(premature)
    local red = get_redis_client()
    if not red then
        return
    end

    local current_time = ngx.now()
    local timestamp = math.floor(current_time)

    -- Snapshot unique IPs
    local unique_ips_count, err = red:scard("unique_ips")
    if unique_ips_count then
        red:lpush("unique_ips_history", timestamp .. ":" .. unique_ips_count)
        red:ltrim("unique_ips_history", 0, 1439) -- Keep last 24 hours (1440 minutes)
    else
        ngx.log(ngx.ERR, "failed to get unique_ips count: ", err)
    end

    -- Snapshot total requests for 24h
    local total_requests_count_str, err = red:get("total_requests_24h")
    local total_requests_count = tonumber(total_requests_count_str) or 0
    if total_requests_count_str then
        red:lpush("total_requests_24h_history", timestamp .. ":" .. total_requests_count)
        red:ltrim("total_requests_24h_history", 0, 1439) -- Keep last 24 hours (1440 minutes)
    else
        ngx.log(ngx.ERR, "failed to get total_requests_24h: ", err)
    end

    red:close()

    -- Reschedule the timer for the next minute
    local ok, err = ngx.timer.at(60, snapshot_history)
    if not ok then
        ngx.log(ngx.ERR, "failed to schedule snapshot_history: ", err)
    end
end

-- Schedule the first timer call if not already scheduled
local ok, err = ngx.timer.at(0, snapshot_history)
if not ok then
    ngx.log(ngx.ERR, "failed to schedule initial snapshot_history: ", err)
end

local function check_blacklist_ip(red, waf_mode)
    local ip_blacklist = get_ip_blacklist(red)
    local remote_addr = ngx.var.remote_addr
    if ip_blacklist[remote_addr] then
        ngx.log(ngx.ERR, "Blocked blacklisted IP: ", remote_addr)
        if waf_mode == "block" then
            ngx.exit(ngx.HTTP_FORBIDDEN)
        end
    end
end

local function check_sql_injection(red, waf_mode, args)
    local sql_patterns = get_sql_patterns(red)
    for _, pattern in ipairs(sql_patterns) do
        if ngx.re.find(args, pattern, "ijo") then
            ngx.log(ngx.ERR, "Blocked SQL injection attempt: ", args)
            if waf_mode == "block" then
                ngx.exit(ngx.HTTP_FORBIDDEN)
            end
        end
    end
end

local function check_xss(red, waf_mode, args)
    local xss_patterns = get_xss_patterns(red)
    for _, pattern in ipairs(xss_patterns) do
        if ngx.re.find(args, pattern, "ijo") then
            ngx.log(ngx.ERR, "Blocked XSS attempt: ", args)
            if waf_mode == "block" then
                ngx.exit(ngx.HTTP_FORBIDDEN)
            end
        end
    end
end

local function waf_main()
    log_access_data() -- Log access data before applying WAF rules

    local red = get_redis_client()
    if not red then
        return
    end

    local waf_mode = get_waf_mode(red)

    check_blacklist_ip(red, waf_mode)

    local uri_args = ngx.var.args
    if uri_args then
        check_sql_injection(red, waf_mode, uri_args)
        check_xss(red, waf_mode, uri_args)
    end

    local request_body = ngx.req.get_body_data()
    if request_body then
        check_sql_injection(red, waf_mode, request_body)
        check_xss(red, waf_mode, request_body)
    end

    red:close()
end

waf_main()
