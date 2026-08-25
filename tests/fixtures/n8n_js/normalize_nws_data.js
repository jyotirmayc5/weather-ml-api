// n8n Code Node
// Robust NWS Gridpoint Normalizer - Next Forecast Hour Only

let raw =
  $input.first().json.data ||
  $input.first().json.body ||
  $input.first().json;

if (typeof raw === "string") {
  raw = JSON.parse(raw);
}

const props = raw.properties;

if (!props) {
  throw new Error("No properties found. Check HTTP Request response format.");
}

if (!props.temperature || !Array.isArray(props.temperature.values)) {
  throw new Error(
    "No temperature.values found. Use https://api.weather.gov/gridpoints/OKX/33,37 and set HTTP Response Format to JSON."
  );
}

const fields = {
  temperature: "temperature_c",
  dewpoint: "dewpoint_c",
  relativeHumidity: "humidity_pct",
  apparentTemperature: "feels_like_c",
  windSpeed: "wind_speed",
  windDirection: "wind_direction",
  probabilityOfPrecipitation: "precip_probability_pct",
  quantitativePrecipitation: "precip_mm",
  snowfallAmount: "snowfall_mm",
  iceAccumulation: "ice_mm",
  skyCover: "sky_cover_pct"
};

function durationToHours(duration) {
  if (!duration) return 1;

  const dayMatch = duration.match(/P(\d+)D/);
  const hourMatch = duration.match(/T(\d+)H/);
  const minMatch = duration.match(/T(\d+)M/);

  let hours = 0;

  if (dayMatch) hours += Number(dayMatch[1]) * 24;
  if (hourMatch) hours += Number(hourMatch[1]);
  if (minMatch) hours += Math.ceil(Number(minMatch[1]) / 60);

  return hours || 1;
}

function addHours(date, hours) {
  return new Date(date.getTime() + hours * 3600000);
}

function toNumber(value, fallback = null) {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "number") return value;

  if (typeof value === "string") {
    const match = value.match(/-?\d+(\.\d+)?/);
    return match ? Number(match[0]) : fallback;
  }

  return fallback;
}

const rows = {};

for (const [sourceField, outputField] of Object.entries(fields)) {
  const series = props[sourceField]?.values;

  if (!Array.isArray(series)) continue;

  for (const item of series) {
    if (!item.validTime) continue;

    const [startRaw, durationRaw = "PT1H"] = item.validTime.split("/");
    const start = new Date(startRaw);
    const hours = durationToHours(durationRaw);

    for (let h = 0; h < hours; h++) {
      const ts = addHours(start, h).toISOString();

      if (!rows[ts]) {
        rows[ts] = {
          timestamp: ts,
          forecast_created_at: new Date().toISOString(),
          office: props.gridId || null,
          gridX: props.gridX || null,
          gridY: props.gridY || null,
          source: "NWS"
        };
      }

      rows[ts][outputField] = toNumber(item.value, null);
    }
  }
}

const now = new Date();

const output = Object.values(rows)
.filter(row => {
  const ts = new Date(row.timestamp);
  const next24h = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  return ts > now && ts <= next24h;
})
.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
  .map(row => ({
    timestamp: row.timestamp,
    forecast_created_at: row.forecast_created_at,

    office: row.office,
    gridX: row.gridX,
    gridY: row.gridY,
    source: row.source,

    temperature_c: row.temperature_c ?? 0,
    dewpoint_c: row.dewpoint_c ?? 0,
    humidity_pct: row.humidity_pct ?? 50,
    feels_like_c: row.feels_like_c ?? row.temperature_c ?? 0,
    wind_speed: row.wind_speed ?? 5,
    wind_direction: row.wind_direction ?? 180,
    precip_probability_pct: row.precip_probability_pct ?? 0,
    precip_mm: row.precip_mm ?? 0,
    snowfall_mm: row.snowfall_mm ?? 0,
    ice_mm: row.ice_mm ?? 0,
    sky_cover_pct: row.sky_cover_pct ?? 50
  }));

return output.map(row => ({
  json: row
}));