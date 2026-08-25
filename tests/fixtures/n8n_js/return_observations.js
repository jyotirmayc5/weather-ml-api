return $input.all().map(item => {

  const p = item.json.properties;

  const cToF = (c) =>
    c === null || c === undefined
      ? null
      : Number(((c * 9 / 5) + 32).toFixed(1));

  const pressurePa = p.barometricPressure?.value ?? null;
  const windSpeed = p.windSpeed?.value ?? null;
  const windDirection = p.windDirection?.value ?? null;

  return {
    json: {
      observed_time: p.timestamp,

      station:
        p.station?.split("/").pop() ?? null,

      actual_temperature_f:
        cToF(p.temperature?.value),

      actual_dewpoint_f:
        cToF(p.dewpoint?.value),

      actual_humidity_pct:
        p.relativeHumidity?.value ?? null,

      actual_pressure_pa:
        pressurePa,

      actual_pressure_hpa:
        pressurePa == null
          ? null
          : Number((pressurePa / 100).toFixed(1)),

      actual_wind_speed:
        windSpeed,

      actual_wind_direction:
        windDirection,

      wind_u:
        windSpeed == null || windDirection == null
          ? null
          : Number((windSpeed * Math.sin(windDirection * Math.PI / 180)).toFixed(2)),

      wind_v:
        windSpeed == null || windDirection == null
          ? null
          : Number((windSpeed * Math.cos(windDirection * Math.PI / 180)).toFixed(2)),

      visibility_m:
        p.visibility?.value ?? null,

      text_description:
        p.textDescription ?? null
    }
  };
});