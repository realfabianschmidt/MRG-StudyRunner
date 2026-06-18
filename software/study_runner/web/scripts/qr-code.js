const ECL_MEDIUM_FORMAT_BITS = 0;

const VERSION_SPECS = [
  null,
  { version: 1, size: 21, dataCodewords: 16, ecCodewords: 10, blocks: [16], alignment: [] },
  { version: 2, size: 25, dataCodewords: 28, ecCodewords: 16, blocks: [28], alignment: [6, 18] },
  { version: 3, size: 29, dataCodewords: 44, ecCodewords: 26, blocks: [44], alignment: [6, 22] },
  { version: 4, size: 33, dataCodewords: 64, ecCodewords: 18, blocks: [32, 32], alignment: [6, 26] },
  { version: 5, size: 37, dataCodewords: 86, ecCodewords: 24, blocks: [43, 43], alignment: [6, 30] },
  { version: 6, size: 41, dataCodewords: 108, ecCodewords: 16, blocks: [27, 27, 27, 27], alignment: [6, 34] },
];

let gfExp = null;
let gfLog = null;
const generatorCache = new Map();

export function createQrSvg(text, options = {}) {
  const margin = options.margin ?? 4;
  const pixelSize = options.size ?? 240;
  const modules = createQrModules(text);
  const moduleCount = modules.length;
  const viewBoxSize = moduleCount + margin * 2;
  const path = [];

  modules.forEach((row, y) => {
    row.forEach((isDark, x) => {
      if (isDark) {
        path.push(`M${x + margin} ${y + margin}h1v1h-1z`);
      }
    });
  });

  return [
    `<svg class="access-qr-svg" xmlns="http://www.w3.org/2000/svg" width="${pixelSize}" height="${pixelSize}" viewBox="0 0 ${viewBoxSize} ${viewBoxSize}" role="img" aria-hidden="true" shape-rendering="crispEdges">`,
    `<rect width="${viewBoxSize}" height="${viewBoxSize}" fill="#fff"/>`,
    `<path fill="#08080A" d="${path.join('')}"/>`,
    `</svg>`,
  ].join('');
}

function createQrModules(text) {
  const bytes = Array.from(new TextEncoder().encode(String(text)));
  const spec = pickVersionSpec(bytes.length);
  const dataCodewords = buildDataCodewords(bytes, spec);
  const finalCodewords = addErrorCorrection(dataCodewords, spec);
  const base = createBaseMatrix(spec);
  drawCodewords(base.modules, base.functionModules, finalCodewords);

  let bestModules = null;
  let bestPenalty = Infinity;
  for (let mask = 0; mask < 8; mask += 1) {
    const candidate = cloneMatrix(base.modules);
    applyMask(candidate, base.functionModules, mask);
    drawFormatBits(candidate, base.functionModules, mask);
    const penalty = calculatePenalty(candidate);
    if (penalty < bestPenalty) {
      bestPenalty = penalty;
      bestModules = candidate;
    }
  }

  return bestModules;
}

function pickVersionSpec(byteLength) {
  for (let version = 1; version < VERSION_SPECS.length; version += 1) {
    const spec = VERSION_SPECS[version];
    const capacity = Math.floor((spec.dataCodewords * 8 - 12) / 8);
    if (byteLength <= capacity) {
      return spec;
    }
  }
  throw new Error('Text is too long for the local QR code renderer.');
}

function buildDataCodewords(bytes, spec) {
  const bits = [];
  appendBits(bits, 0x4, 4);
  appendBits(bits, bytes.length, 8);
  bytes.forEach((byte) => appendBits(bits, byte, 8));

  const capacityBits = spec.dataCodewords * 8;
  appendBits(bits, 0, Math.min(4, capacityBits - bits.length));
  while (bits.length % 8 !== 0) bits.push(0);

  const codewords = [];
  for (let i = 0; i < bits.length; i += 8) {
    codewords.push(bitsToByte(bits.slice(i, i + 8)));
  }

  const padBytes = [0xEC, 0x11];
  let padIndex = 0;
  while (codewords.length < spec.dataCodewords) {
    codewords.push(padBytes[padIndex % 2]);
    padIndex += 1;
  }
  return codewords;
}

function addErrorCorrection(dataCodewords, spec) {
  const blocks = [];
  let offset = 0;
  spec.blocks.forEach((blockLength) => {
    const data = dataCodewords.slice(offset, offset + blockLength);
    offset += blockLength;
    blocks.push({ data, ec: reedSolomonRemainder(data, spec.ecCodewords) });
  });

  const result = [];
  const maxDataLength = Math.max(...blocks.map((block) => block.data.length));
  for (let i = 0; i < maxDataLength; i += 1) {
    blocks.forEach((block) => {
      if (i < block.data.length) result.push(block.data[i]);
    });
  }
  for (let i = 0; i < spec.ecCodewords; i += 1) {
    blocks.forEach((block) => result.push(block.ec[i]));
  }
  return result;
}

function createBaseMatrix(spec) {
  const modules = makeMatrix(spec.size, false);
  const functionModules = makeMatrix(spec.size, false);

  drawFinderPattern(modules, functionModules, 0, 0);
  drawFinderPattern(modules, functionModules, spec.size - 7, 0);
  drawFinderPattern(modules, functionModules, 0, spec.size - 7);
  drawAlignmentPatterns(modules, functionModules, spec.alignment);
  drawTimingPatterns(modules, functionModules);
  reserveFormatBits(functionModules);
  setFunctionModule(modules, functionModules, 8, spec.size - 8, true);

  return { modules, functionModules };
}

function drawFinderPattern(modules, functionModules, left, top) {
  const size = modules.length;
  for (let y = -1; y <= 7; y += 1) {
    for (let x = -1; x <= 7; x += 1) {
      const moduleX = left + x;
      const moduleY = top + y;
      if (moduleX < 0 || moduleY < 0 || moduleX >= size || moduleY >= size) continue;
      const inPattern = x >= 0 && x <= 6 && y >= 0 && y <= 6;
      const isDark = inPattern && (
        x === 0 || x === 6 || y === 0 || y === 6 || (x >= 2 && x <= 4 && y >= 2 && y <= 4)
      );
      setFunctionModule(modules, functionModules, moduleX, moduleY, isDark);
    }
  }
}

function drawAlignmentPatterns(modules, functionModules, centers) {
  centers.forEach((centerY) => {
    centers.forEach((centerX) => {
      if (functionModules[centerY][centerX]) return;
      for (let y = -2; y <= 2; y += 1) {
        for (let x = -2; x <= 2; x += 1) {
          const distance = Math.max(Math.abs(x), Math.abs(y));
          setFunctionModule(modules, functionModules, centerX + x, centerY + y, distance === 0 || distance === 2);
        }
      }
    });
  });
}

function drawTimingPatterns(modules, functionModules) {
  const size = modules.length;
  for (let i = 8; i < size - 8; i += 1) {
    const isDark = i % 2 === 0;
    setFunctionModule(modules, functionModules, i, 6, isDark);
    setFunctionModule(modules, functionModules, 6, i, isDark);
  }
}

function reserveFormatBits(functionModules) {
  const size = functionModules.length;
  for (let i = 0; i <= 5; i += 1) functionModules[i][8] = true;
  functionModules[7][8] = true;
  functionModules[8][8] = true;
  functionModules[8][7] = true;
  for (let i = 0; i <= 5; i += 1) functionModules[8][i] = true;
  for (let i = 0; i < 8; i += 1) functionModules[8][size - 1 - i] = true;
  for (let i = 0; i < 7; i += 1) functionModules[size - 1 - i][8] = true;
}

function drawCodewords(modules, functionModules, codewords) {
  const bits = [];
  codewords.forEach((codeword) => appendBits(bits, codeword, 8));

  const size = modules.length;
  let bitIndex = 0;
  let upward = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right -= 1;
    for (let vertical = 0; vertical < size; vertical += 1) {
      const y = upward ? size - 1 - vertical : vertical;
      for (let offset = 0; offset < 2; offset += 1) {
        const x = right - offset;
        if (!functionModules[y][x]) {
          modules[y][x] = bitIndex < bits.length ? Boolean(bits[bitIndex]) : false;
          bitIndex += 1;
        }
      }
    }
    upward = !upward;
  }
}

function applyMask(modules, functionModules, mask) {
  modules.forEach((row, y) => {
    row.forEach((_, x) => {
      if (!functionModules[y][x] && maskCondition(mask, x, y)) {
        modules[y][x] = !modules[y][x];
      }
    });
  });
}

function maskCondition(mask, x, y) {
  switch (mask) {
    case 0: return (x + y) % 2 === 0;
    case 1: return y % 2 === 0;
    case 2: return x % 3 === 0;
    case 3: return (x + y) % 3 === 0;
    case 4: return (Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0;
    case 5: return ((x * y) % 2 + (x * y) % 3) === 0;
    case 6: return (((x * y) % 2 + (x * y) % 3) % 2) === 0;
    case 7: return (((x + y) % 2 + (x * y) % 3) % 2) === 0;
    default: return false;
  }
}

function drawFormatBits(modules, functionModules, mask) {
  const bits = getFormatBits((ECL_MEDIUM_FORMAT_BITS << 3) | mask);
  const size = modules.length;
  for (let i = 0; i <= 5; i += 1) setFunctionModule(modules, functionModules, 8, i, getBit(bits, i));
  setFunctionModule(modules, functionModules, 8, 7, getBit(bits, 6));
  setFunctionModule(modules, functionModules, 8, 8, getBit(bits, 7));
  setFunctionModule(modules, functionModules, 7, 8, getBit(bits, 8));
  for (let i = 9; i < 15; i += 1) setFunctionModule(modules, functionModules, 14 - i, 8, getBit(bits, i));
  for (let i = 0; i < 8; i += 1) setFunctionModule(modules, functionModules, size - 1 - i, 8, getBit(bits, i));
  for (let i = 8; i < 15; i += 1) setFunctionModule(modules, functionModules, 8, size - 15 + i, getBit(bits, i));
  setFunctionModule(modules, functionModules, 8, size - 8, true);
}

function getFormatBits(value) {
  let remainder = value << 10;
  for (let i = 14; i >= 10; i -= 1) {
    if (((remainder >>> i) & 1) !== 0) {
      remainder ^= 0x537 << (i - 10);
    }
  }
  return ((value << 10) | remainder) ^ 0x5412;
}

function calculatePenalty(modules) {
  return (
    calculateRunPenalty(modules) +
    calculateBlockPenalty(modules) +
    calculateFinderPenalty(modules) +
    calculateBalancePenalty(modules)
  );
}

function calculateRunPenalty(modules) {
  let penalty = 0;
  const size = modules.length;
  for (let y = 0; y < size; y += 1) penalty += countRunPenalty(modules[y]);
  for (let x = 0; x < size; x += 1) penalty += countRunPenalty(modules.map((row) => row[x]));
  return penalty;
}

function countRunPenalty(line) {
  let penalty = 0;
  let runColor = line[0];
  let runLength = 1;
  for (let i = 1; i < line.length; i += 1) {
    if (line[i] === runColor) {
      runLength += 1;
    } else {
      if (runLength >= 5) penalty += 3 + runLength - 5;
      runColor = line[i];
      runLength = 1;
    }
  }
  if (runLength >= 5) penalty += 3 + runLength - 5;
  return penalty;
}

function calculateBlockPenalty(modules) {
  let penalty = 0;
  for (let y = 0; y < modules.length - 1; y += 1) {
    for (let x = 0; x < modules.length - 1; x += 1) {
      const color = modules[y][x];
      if (modules[y][x + 1] === color && modules[y + 1][x] === color && modules[y + 1][x + 1] === color) {
        penalty += 3;
      }
    }
  }
  return penalty;
}

function calculateFinderPenalty(modules) {
  let penalty = 0;
  const size = modules.length;
  for (let y = 0; y < size; y += 1) penalty += countFinderPatternPenalty(modules[y]);
  for (let x = 0; x < size; x += 1) penalty += countFinderPatternPenalty(modules.map((row) => row[x]));
  return penalty;
}

function countFinderPatternPenalty(line) {
  const patterns = [
    [true, false, true, true, true, false, true, false, false, false, false],
    [false, false, false, false, true, false, true, true, true, false, true],
  ];
  let penalty = 0;
  for (let i = 0; i <= line.length - 11; i += 1) {
    if (patterns.some((pattern) => pattern.every((value, offset) => line[i + offset] === value))) {
      penalty += 40;
    }
  }
  return penalty;
}

function calculateBalancePenalty(modules) {
  const total = modules.length * modules.length;
  const dark = modules.reduce((sum, row) => sum + row.filter(Boolean).length, 0);
  return Math.floor(Math.abs((dark * 100) / total - 50) / 5) * 10;
}

function reedSolomonRemainder(data, degree) {
  initGaloisField();
  const generator = getGeneratorPolynomial(degree);
  const result = new Array(degree).fill(0);
  data.forEach((byte) => {
    const factor = byte ^ result.shift();
    result.push(0);
    for (let i = 0; i < degree; i += 1) {
      result[i] ^= gfMultiply(generator[i + 1], factor);
    }
  });
  return result;
}

function getGeneratorPolynomial(degree) {
  if (generatorCache.has(degree)) return generatorCache.get(degree);
  let poly = [1];
  for (let i = 0; i < degree; i += 1) {
    poly = multiplyPolynomials(poly, [1, gfExp[i]]);
  }
  generatorCache.set(degree, poly);
  return poly;
}

function multiplyPolynomials(left, right) {
  const result = new Array(left.length + right.length - 1).fill(0);
  left.forEach((leftValue, leftIndex) => {
    right.forEach((rightValue, rightIndex) => {
      result[leftIndex + rightIndex] ^= gfMultiply(leftValue, rightValue);
    });
  });
  return result;
}

function initGaloisField() {
  if (gfExp && gfLog) return;
  gfExp = new Array(512).fill(0);
  gfLog = new Array(256).fill(0);
  let value = 1;
  for (let i = 0; i < 255; i += 1) {
    gfExp[i] = value;
    gfLog[value] = i;
    value <<= 1;
    if (value & 0x100) value ^= 0x11D;
  }
  for (let i = 255; i < 512; i += 1) gfExp[i] = gfExp[i - 255];
}

function gfMultiply(left, right) {
  if (left === 0 || right === 0) return 0;
  return gfExp[gfLog[left] + gfLog[right]];
}

function appendBits(target, value, length) {
  for (let i = length - 1; i >= 0; i -= 1) {
    target.push((value >>> i) & 1);
  }
}

function bitsToByte(bits) {
  return bits.reduce((value, bit) => (value << 1) | bit, 0);
}

function getBit(value, index) {
  return ((value >>> index) & 1) !== 0;
}

function makeMatrix(size, value) {
  return Array.from({ length: size }, () => new Array(size).fill(value));
}

function cloneMatrix(matrix) {
  return matrix.map((row) => [...row]);
}

function setFunctionModule(modules, functionModules, x, y, isDark) {
  modules[y][x] = Boolean(isDark);
  functionModules[y][x] = true;
}
