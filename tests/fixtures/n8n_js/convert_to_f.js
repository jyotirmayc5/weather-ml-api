return items.map(item => {

  const input = item.json.input_received || {};

  const forecastC =
    item.json.forecast_temperature_c ??
    input.temperature_c ??
    item.json.temperature_c ??
    0;

  const correctedC =
    item.json.corrected_temperature_c ??
    forecastC;

  const forecastTime =
    item.json.timestamp ??
    input.timestamp ??
    null;

  const cToF = (c) =>
    c === null || c === undefined
      ? null
      : Number(((c * 9 / 5) + 32).toFixed(1));

  return {
    json: {
      forecast_time: forecastTime,
      forecast_created_at: new Date().toISOString(),

      forecast_temperature_c: forecastC,
      corrected_temperature_c: correctedC,

      forecast_temperature_f: cToF(forecastC),
      corrected_temperature_f: cToF(correctedC),

      predicted_error_c: item.json.predicted_error_c ?? null,
      predicted_error_f: cToF(item.json.predicted_error_c) ?? null,

      dewpoint_c:
        item.json.dewpoint_c ??
        input.dewpoint_c ??
        null,

      dewpoint_f:
        cToF(item.json.dewpoint_c ?? input.dewpoint_c ?? null),

      humidity_pct:
        item.json.humidity_pct ??
        input.humidity_pct ??
        null,

      wind_speed:
        item.json.wind_speed ??
        input.wind_speed ??
        null,

      wind_direction:
        item.json.wind_direction ??
        input.wind_direction ??
        null,

      sky_cover_pct:
        item.json.sky_cover_pct ??
        input.sky_cover_pct ??
        null,

      precip_probability_pct:
        item.json.precip_probability_pct ??
        input.precip_probability_pct ??
        null,

      source:
        item.json.source ??
        input.source ??
        "NWS"
    }
  };
});