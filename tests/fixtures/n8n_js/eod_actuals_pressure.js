const raw = $input.first().json;
const features = raw.features || [];

const cToF = (c) =>
  Number(((c * 9 / 5) + 32).toFixed(1));

const targetDate = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
}).format(new Date());

// Extract station from first feature
const station =
  features[0]?.properties?.station
    ?.split("/")
    .pop() ?? "UNKNOWN";

const temps = features
  .map(f => f.properties?.temperature?.value)
  .filter(v => v !== null && v !== undefined)
  .map(cToF);

if (temps.length === 0) {
  throw new Error(
    `No observations found for ${targetDate}`
  );
}

const maxTemp = Math.max(...temps);

// -----------------------------
// PRESSURE FEATURES
// -----------------------------
const pressureObs = features
  .map(f => {
    const p = f.properties;
    const pressurePa = p?.barometricPressure?.value;
    const timestamp = p?.timestamp;

    if (pressurePa == null || !timestamp) return null;

    const hourNY = Number(
      new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        hour: "2-digit",
        hour12: false
      }).format(new Date(timestamp))
    );

    return {
      pressure_hpa: pressurePa / 100,
      hourNY
    };
  })
  .filter(Boolean);

const avg = (arr) =>
  arr.length
    ? Number((arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1))
    : null;

// Average pressure for entire day
const avgPressureHpa =
  avg(pressureObs.map(o => o.pressure_hpa));

// Closest pressure reading to target hour
function nearestPressure(targetHour) {
  const candidates = pressureObs
    .map(o => ({
      pressure_hpa: o.pressure_hpa,
      diff: Math.abs(o.hourNY - targetHour)
    }))
    .sort((a, b) => a.diff - b.diff);

  return candidates.length
    ? Number(candidates[0].pressure_hpa.toFixed(1))
    : null;
}

const pressure6amHpa = nearestPressure(6);
const pressure12pmHpa = nearestPressure(12);
const pressure6pmHpa = nearestPressure(18);

// Morning = 6 AM to 11:59 AM
const morningPressures = pressureObs
  .filter(o => o.hourNY >= 6 && o.hourNY < 12)
  .map(o => o.pressure_hpa);

// Afternoon = 12 PM to 5:59 PM
const afternoonPressures = pressureObs
  .filter(o => o.hourNY >= 12 && o.hourNY < 18)
  .map(o => o.pressure_hpa);

const morningPressureHpa = avg(morningPressures);
const afternoonPressureHpa = avg(afternoonPressures);

const pressureChangeHpa =
  morningPressureHpa != null && afternoonPressureHpa != null
    ? Number((afternoonPressureHpa - morningPressureHpa).toFixed(1))
    : null;

return [{
  json: {
    target_date: targetDate,

    station: station,

    actual_high_f:
      Number(maxTemp.toFixed(1)),

    morning_pressure_hpa: morningPressureHpa,
    afternoon_pressure_hpa: afternoonPressureHpa,
    pressure_change_hpa: pressureChangeHpa,

    avg_pressure_hpa: avgPressureHpa,
    pressure_6am_hpa: pressure6amHpa,
    pressure_12pm_hpa: pressure12pmHpa,
    pressure_6pm_hpa: pressure6pmHpa,

    observations_used:
      temps.length
  }
}];