'use strict';
// Monitoraggio LOCALE del PC: nulla di tutto questo viene inviato al server.
// - uso del processore da parte degli altri programmi
// - inattività dell'utente (dove il sistema lo permette)
// - batteria
const os = require('os');
const fs = require('fs');
const { execFile } = require('child_process');

function cpuTimes() {
  let idle = 0, total = 0;
  for (const c of os.cpus()) {
    for (const v of Object.values(c.times)) total += v;
    idle += c.times.idle;
  }
  return { idle, total };
}

function run(cmd, args, timeout = 4000) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, windowsHide: true }, (err, stdout) => resolve(err ? null : String(stdout)));
  });
}

async function userIdleSeconds() {
  const p = process.platform;
  if (p === 'darwin') {
    const out = await run('ioreg', ['-c', 'IOHIDSystem']);
    const m = out && out.match(/"HIDIdleTime"\s*=\s*(\d+)/);
    return m ? Number(m[1]) / 1e9 : null;
  }
  if (p === 'win32') {
    const ps = 'Add-Type @"\nusing System;using System.Runtime.InteropServices;public class I{[StructLayout(LayoutKind.Sequential)]public struct L{public uint s;public uint t;}[DllImport("user32.dll")]public static extern bool GetLastInputInfo(ref L l);public static uint Idle(){L l=new L();l.s=(uint)Marshal.SizeOf(l);GetLastInputInfo(ref l);return ((uint)Environment.TickCount-l.t)/1000;}}\n"@\n[I]::Idle()';
    const out = await run('powershell', ['-NoProfile', '-NonInteractive', '-Command', ps], 8000);
    const n = out && Number(out.trim());
    return Number.isFinite(n) ? n : null;
  }
  if (p === 'linux') {
    const out = await run('xprintidle', []);
    const n = out && Number(out.trim());
    return Number.isFinite(n) ? n / 1000 : null;
  }
  return null;
}

async function battery() {
  const p = process.platform;
  try {
    if (p === 'linux') {
      const base = '/sys/class/power_supply';
      for (const d of fs.readdirSync(base)) {
        if (!d.startsWith('BAT')) continue;
        const status = fs.readFileSync(`${base}/${d}/status`, 'utf8').trim();
        const cap = Number(fs.readFileSync(`${base}/${d}/capacity`, 'utf8').trim());
        return { present: true, onBattery: status === 'Discharging', percent: cap };
      }
      return { present: false, onBattery: false, percent: null };
    }
    if (p === 'darwin') {
      const out = await run('pmset', ['-g', 'batt']);
      if (!out || !/InternalBattery/.test(out)) return { present: false, onBattery: false, percent: null };
      const m = out.match(/(\d+)%/);
      return { present: true, onBattery: /Battery Power/.test(out), percent: m ? Number(m[1]) : null };
    }
    if (p === 'win32') {
      const out = await run('powershell', ['-NoProfile', '-NonInteractive', '-Command',
        '$b=Get-CimInstance Win32_Battery; if($b){"$($b.BatteryStatus) $($b.EstimatedChargeRemaining)"}'], 8000);
      if (!out || !out.trim()) return { present: false, onBattery: false, percent: null };
      const [st, pc] = out.trim().split(/\s+/).map(Number);
      return { present: true, onBattery: st === 1, percent: pc };
    }
  } catch { /* nessuna batteria leggibile */ }
  return { present: false, onBattery: false, percent: null };
}

class Monitor {
  constructor() {
    this.prev = cpuTimes();
    this.prevSelf = process.cpuUsage();
    this.prevAt = Date.now();
    this.othersLoad = 0;      // 0..1, carico degli altri programmi
    this.idleSeconds = null;  // null = non rilevabile
    this.battery = { present: false, onBattery: false, percent: null };
    this.slowTick = 0;
  }

  async sample() {
    const cur = cpuTimes();
    const self = process.cpuUsage();
    const now = Date.now();
    const dTotal = cur.total - this.prev.total;
    const dIdle = cur.idle - this.prev.idle;
    const busyAll = dTotal > 0 ? 1 - dIdle / dTotal : 0;
    // tempo CPU del nodo (thread compresi) rispetto alla capacità totale
    const selfMs = (self.user - this.prevSelf.user + self.system - this.prevSelf.system) / 1000;
    const capMs = (now - this.prevAt) * os.cpus().length;
    const busySelf = capMs > 0 ? selfMs / capMs : 0;
    this.othersLoad = Math.max(0, Math.min(1, busyAll - busySelf));
    this.prev = cur; this.prevSelf = self; this.prevAt = now;
    // i controlli lenti (programmi esterni) solo ogni 6 campioni
    if (this.slowTick++ % 6 === 0) {
      this.idleSeconds = await userIdleSeconds();
      this.battery = await battery();
    }
    return this.snapshot();
  }

  snapshot() {
    return { othersLoad: this.othersLoad, idleSeconds: this.idleSeconds, battery: this.battery };
  }
}

module.exports = { Monitor };
