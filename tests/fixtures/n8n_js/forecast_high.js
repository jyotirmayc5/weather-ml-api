let raw =
  $input.first().json.data ||
  $input.first().json.body ||
  $input.first().json;

if (typeof raw === "string") raw = JSON.parse(raw);

const props = raw.properties;

function cToF(c) {
  return Number(((c * 9 / 5) + 32).toFixed(1));
}

function durationToHours(duration) {
  if (!duration) return 1;
  const dayMatch = duration.match(/P(\d+)D/);
  const hourMatch = duration.match(/T(\d+)H/);
  let hours = 0;
  if (dayMatch) hours += Number(dayMatch[1]) * 24;
  if (hourMatch) hours += Number(hourMatch[1]);
  return hours || 1;
}

function addHours(date, hours) {
  return new Date(date.getTime() + hours * 3600000);
}

const now = new Date();

const nyDate = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
}).format(new Date(now.getTime() + 24 * 60 * 60 * 1000));

const rows = {};

const fields = {
  temperature: "temperature_c",
  dewpoint: "dewpoint_c",
  relativeHumidity: "humidity_pct",
  skyCover: "sky_cover_pct",
  probabilityOfPrecipitation: "precip_probability_pct",
  windSpeed: "wind_speed",
  windDirection: "wind_direction"
};

for (const [sourceField, outputField] of Object.entries(fields)) {
  const series = props[sourceField]?.values || [];

  for (const item of series) {
    const [startRaw, durationRaw = "PT1H"] = item.validTime.split("/");
    const start = new Date(startRaw);
    const hours = durationToHours(durationRaw);

    for (let h = 0; h < hours; h++) {
      const ts = addHours(start, h);

      const localDate = new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/New_York",
        year: "numeric",
        month: "2-digit",
        day: "2-digit"
      }).format(ts);

      if (localDate !== nyDate) continue;

      const key = ts.toISOString();

      if (!rows[key]) rows[key] = { timestamp: key };

      rows[key][outputField] = item.value;
    }
  }
}

const dayRows = Object.values(rows);

const tempsF = dayRows
  .map(r => r.temperature_c)
  .filter(v => v !== null && v !== undefined)
  .map(cToF);

const avg = arr =>
  arr.length ? Number((arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1)) : null;

const max = arr =>
  arr.length ? Math.max(...arr) : null;

const degToRad = deg => deg * Math.PI / 180;

const vals = field =>
  dayRows.map(r => r[field]).filter(v => v !== null && v !== undefined);

const avgSinWind = avg(
  vals("wind_direction").map(d => Math.sin(degToRad(d)))
);

const avgCosWind = avg(
  vals("wind_direction").map(d => Math.cos(degToRad(d)))
);

const hourNY = iso =>
  Number(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    hour12: false
  }).format(new Date(iso)));

const peakRows = dayRows.filter(r => {
  const h = hourNY(r.timestamp);
  return h >= 12 && h <= 16;
});

const peakVals = field =>
  peakRows.map(r => r[field]).filter(v => v !== null && v !== undefined);

const targetEndUTC = new Date(
  new Date(`${nyDate}T23:59:59`).toLocaleString(
    "en-US",
    { timeZone: "America/New_York" }
  )
);

const leadHours = Number(
  ((targetEndUTC - now) / 3600000).toFixed(1)
);

const nowNY = new Date();

const month = Number(
  new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "numeric"
  }).format(nowNY)
);

const start = new Date(nowNY.getFullYear(), 0, 0);
const diff = nowNY - start;
const oneDay = 1000 * 60 * 60 * 24;

const dayOfYear = Math.floor(diff / oneDay);

return [{
  json: {
    prediction_created_at: now.toISOString(),
    target_date: nyDate,
    station: "KNYC",

    forecast_high_f: max(tempsF),
    forecast_low_f: tempsF.length ? Math.min(...tempsF) : null,
    corrected_high_f: null,

    avg_humidity_pct: avg(vals("humidity_pct")),
    avg_dewpoint_f: avg(vals("dewpoint_c").map(cToF)),
    avg_sky_cover_pct: avg(vals("sky_cover_pct")),
    max_precip_probability_pct: max(vals("precip_probability_pct")),

    peak_heating_cloud_pct: avg(peakVals("sky_cover_pct")),
    peak_heating_temp_f: max(peakVals("temperature_c").map(cToF)),

    avg_wind_speed: avg(vals("wind_speed")),
    avg_wind_sin: avgSinWind,
    avg_wind_cos: avgCosWind,
    lead_hours: leadHours,
    month: month,
    day_of_year: dayOfYear,
    source: "NWS OKX/33,37"
  }
}];