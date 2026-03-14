const fs = require('fs');
const path = require('path');

const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8'));
const depNames = Object.keys(pkg.dependencies || {}).sort((a, b) =>
  a.toLowerCase().localeCompare(b.toLowerCase())
);

for (const name of depNames) {
  const pkgDir = path.join('node_modules', name);
  let version = 'unknown';
  let licenseType = 'Unknown';
  let licenseText = null;

  try {
    const depPkg = JSON.parse(fs.readFileSync(path.join(pkgDir, 'package.json'), 'utf8'));
    version = depPkg.version || version;
    licenseType = depPkg.license || licenseType;
  } catch {}

  try {
    const files = fs.readdirSync(pkgDir);
    const licFile = files.find((file) => /^(licen[sc]e|copying)/i.test(file));
    if (licFile) {
      licenseText = fs.readFileSync(path.join(pkgDir, licFile), 'utf8').trim();
    }
  } catch {}

  console.log(`### ${name} (${version}) — ${licenseType}\n`);
  if (licenseText) {
    console.log('<details>');
    console.log('<summary>Full license text</summary>');
    console.log();
    console.log('```');
    console.log(licenseText);
    console.log('```');
    console.log();
    console.log('</details>');
  } else {
    console.log('*License file not found in package.*');
  }
  console.log();
}
