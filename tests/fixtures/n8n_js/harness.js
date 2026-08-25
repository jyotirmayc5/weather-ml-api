// Runs one extracted n8n Code node script with a controlled input and a frozen
// "now", so its output is fully deterministic and can be captured as a golden
// fixture. Freezing Date only affects no-arg `new Date()` / `Date.now()` --
// `new Date(someString)` still parses normally, so validTime parsing etc. is
// untouched.
//
// IMPORTANT ASSUMPTION: several of these scripts parse plain "YYYY-MM-DDTHH:mm:ss"
// strings (no offset) with `new Date(...)`, which the JS spec resolves using the
// *host system's local timezone*, not a fixed one. That means their output is
// only reproducible if the host timezone is pinned. We assume Render's containers
// run in UTC (Render's documented default) and pin TZ=UTC here to match. If the
// real Render service is ever configured with a different TZ, these fixtures
// stop being faithful and need regenerating -- verify this assumption against
// the actual Render service settings before trusting DST-related fixtures.
process.env.TZ = "UTC";

const fs = require("fs");

const [scriptFile, inputFile, outputFile, fixedNowIso] = process.argv.slice(2);

if (!scriptFile || !inputFile || !outputFile || !fixedNowIso) {
  console.error(
    "usage: node harness.js <scriptFile> <inputFile.json> <outputFile.json> <fixedNowIso>"
  );
  process.exit(1);
}

const fixedNowMs = new Date(fixedNowIso).getTime();
if (Number.isNaN(fixedNowMs)) {
  console.error(`invalid fixedNowIso: ${fixedNowIso}`);
  process.exit(1);
}

const RealDate = Date;

class FrozenDate extends RealDate {
  constructor(...args) {
    if (args.length === 0) {
      super(fixedNowMs);
    } else {
      super(...args);
    }
  }
  static now() {
    return fixedNowMs;
  }
}

global.Date = FrozenDate;

const scriptCode = fs.readFileSync(scriptFile, "utf-8");
const rawInput = JSON.parse(fs.readFileSync(inputFile, "utf-8"));

const items = Array.isArray(rawInput)
  ? rawInput.map((json) => ({ json }))
  : [{ json: rawInput }];

const $input = {
  first: () => items[0],
  all: () => items,
};

const run = new Function("$input", "items", scriptCode);
const result = run($input, items);

fs.writeFileSync(
  outputFile,
  JSON.stringify(
    { fixed_now: fixedNowIso, output: result },
    null,
    2
  )
);

console.log(`wrote ${outputFile}`);
