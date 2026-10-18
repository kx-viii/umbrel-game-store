-- Writes who is online, where they are, the in-game time and the weather to
-- Zomboid/Lua/umbrel/live.txt every few seconds, for the app's dashboard map.
-- start.sh copies this into the server's media/lua/server folder; players don't need it.
if isClient() then return end

local INTERVAL_MS = 5000
local startedAt = getTimestampMs()
local lastWrite = 0

local function str(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("%c", " ")
    return '"' .. s .. '"'
end

local function num(n)
    n = tonumber(n)
    if not n or n ~= n or n == math.huge or n == -math.huge then return "null" end
    return string.format("%.2f", n)
end

local function try(f, ...)
    local ok, value = pcall(f, ...)
    if ok then return value end
    return nil
end

local function playerRow(p)
    local health = try(function() return p:getBodyDamage():getOverallBodyHealth() end)
    local access = try(function() return p:getAccessLevel() end) or ""
    return "{"
        .. '"name":' .. str(p:getUsername())
        .. ',"x":' .. num(p:getX())
        .. ',"y":' .. num(p:getY())
        .. ',"z":' .. num(p:getZ())
        .. ',"health":' .. num(health)
        .. ',"alive":' .. tostring(try(function() return p:isAlive() end) ~= false)
        .. ',"kills":' .. num(try(function() return p:getZombieKills() end))
        .. ',"hoursSurvived":' .. num(try(function() return p:getHoursSurvived() end))
        .. ',"admin":' .. tostring(access ~= "" and string.lower(access) ~= "none")
        .. "}"
end

local function gameTimeJson()
    local gt = getGameTime()
    if not gt then return "null" end
    local tod = try(function() return gt:getTimeOfDay() end) or 0
    local hour = math.floor(tod)
    return "{"
        .. '"year":' .. num(try(function() return gt:getYear() end))
        .. ',"month":' .. num((try(function() return gt:getMonth() end) or 0) + 1)
        .. ',"day":' .. num((try(function() return gt:getDay() end) or 0) + 1)
        .. ',"hour":' .. num(hour)
        .. ',"minute":' .. num(math.floor((tod - hour) * 60))
        .. ',"daysSurvived":' .. num((try(function() return gt:getWorldAgeHours() end) or 0) / 24)
        .. "}"
end

local function weatherJson()
    local c = getClimateManager and getClimateManager()
    if not c then return "null" end
    return "{"
        .. '"temperature":' .. num(try(function() return c:getTemperature() end))
        .. ',"raining":' .. tostring(try(function() return c:isRaining() end) == true)
        .. ',"snowing":' .. tostring(try(function() return c:isSnowing() end) == true)
        .. ',"fog":' .. num(try(function() return c:getFogIntensity() end))
        .. ',"wind":' .. num(try(function() return c:getWindspeedKph() end))
        .. ',"clouds":' .. num(try(function() return c:getCloudIntensity() end))
        .. "}"
end

local function writeLive()
    local rows = {}
    local list = getOnlinePlayers()
    if list then
        for i = 0, list:size() - 1 do
            local p = list:get(i)
            if p then
                local row = try(playerRow, p)
                if row then table.insert(rows, row) end
            end
        end
    end
    local json = "{"
        .. '"now":' .. string.format("%.0f", getTimestampMs())
        .. ',"startedAt":' .. string.format("%.0f", startedAt)
        .. ',"players":[' .. table.concat(rows, ",") .. "]"
        .. ',"gameTime":' .. gameTimeJson()
        .. ',"weather":' .. weatherJson()
        .. "}"
    local writer = getFileWriter("umbrel/live.txt", true, false)
    if writer then
        writer:write(json)
        writer:close()
    end
end

local function onTick()
    local now = getTimestampMs()
    if now - lastWrite < INTERVAL_MS then return end
    lastWrite = now
    pcall(writeLive)
end

Events.OnTickEvenPaused.Add(onTick)
