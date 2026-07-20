import express, { Request, Response } from "express";
import cors from "cors";
import path from "path";
import fs from "fs";
import os from "os";
import { fileURLToPath } from "url";
import { createServer as createViteServer } from "vite";
import { glob } from "glob";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 3000;

app.use(cors());
app.use(express.json());

const PROJECT_ROOT = __dirname;
const DATA_DIR = path.join(PROJECT_ROOT, "DATA_DIR");
const HISTORY_DIR = path.join(DATA_DIR, "history");

// User requested paths
const LOGS_DIR = "/logs";
const HISTORY_ROOT = "/history";
const BINANCE_DIR = "/binance";
const DECISIONS_DIR = "/binance/data/decisions";
const NIELS_BINANCE = "/home/niels/binance";
const NIELS_LOGS = "/home/niels/logs";

const CRYPTO_BOTS = ['ang', 'inf', 'flz', 'men', 'fin'];
const STOCK_BOTS = ['trb', 'trc'];
const ALL_BOTS = [...CRYPTO_BOTS, ...STOCK_BOTS];

// Helper to load JSON safely
const loadJsonSafe = (filepath: string) => {
    if (fs.existsSync(filepath)) {
        try {
            const content = fs.readFileSync(filepath, 'utf-8').trim();
            if (!content) return [];
            const data = JSON.parse(content);
            if (typeof data === 'object' && !Array.isArray(data)) return Object.values(data);
            return data;
        } catch (e) {
            console.error(`Error loading ${filepath}:`, e);
        }
    }
    return [];
};

// API Routes
app.get('/api/history', async (req: Request, res: Response) => {
    const history: any[] = [];
    
    // Scan multiple possible history locations
    const baseHistoryDirs = [HISTORY_ROOT, HISTORY_DIR, path.join(PROJECT_ROOT, "history")];
    
    for (const baseDir of baseHistoryDirs) {
        if (!fs.existsSync(baseDir)) continue;

        for (const bot of ALL_BOTS) {
            const botHistPath = path.join(baseDir, bot);
            if (!fs.existsSync(botHistPath)) continue;

            try {
                const jsonlFiles = await glob(path.join(botHistPath, "*.jsonl"));
                for (const fpath of jsonlFiles) {
                    const fname = path.basename(fpath);
                    const symbol = fname.split('_')[0].replace('.jsonl', '');
                    
                    try {
                        const content = fs.readFileSync(fpath, 'utf-8');
                        const lines = content.split('\n');
                        for (const line of lines) {
                            if (!line.trim()) continue;
                            try {
                                const data = JSON.parse(line);
                                history.push({
                                    "ts": data.ts || data.timestamp,
                                    "type": data.type,
                                    "symbol": symbol,
                                    "bot": bot,
                                    "qty": data.qty || data.amount,
                                    "price": data.price,
                                    "value": data.value || ((data.amount || 0) * (data.price || 0)),
                                    "reason": data.reason || data.reason_text || "Unknown",
                                    "score": data.score || data.tech_score,
                                    "snapshot": data.snapshot || data.indicators || {}
                                });
                            } catch (e) {}
                        }
                    } catch (e) {}
                }
            } catch (e) {
                console.error(`Error scanning history in ${botHistPath}:`, e);
            }
        }
    }
    res.json(history);
});

app.get('/api/rankings', (req: Request, res: Response) => {
    const paths = [
        path.join(PROJECT_ROOT, "ranking_points.json"),
        path.join(DATA_DIR, "ranking_points.json"),
        path.join(BINANCE_DIR, "ranking_points.json"),
        path.join(NIELS_BINANCE, "ranking_points.json")
    ];
    for (const p of paths) {
        if (fs.existsSync(p)) {
            try {
                return res.json(JSON.parse(fs.readFileSync(p, 'utf-8')));
            } catch (e) {}
        }
    }
    res.json({});
});

app.get('/api/positions', (req: Request, res: Response) => {
    const data: any = {};
    const baseDirs = [BINANCE_DIR, PROJECT_ROOT, NIELS_BINANCE];
    
    for (const bot of ALL_BOTS) {
        data[bot] = { long: [], short: [] };
        
        for (const baseDir of baseDirs) {
            const botDir = path.join(baseDir, bot);
            if (!fs.existsSync(botDir)) continue;
            
            const lp = path.join(botDir, "long_positions.json");
            const sp = path.join(botDir, "short_positions.json");
            
            if (fs.existsSync(lp)) {
                const positions = loadJsonSafe(lp);
                data[bot].long.push(...positions);
            }
            if (fs.existsSync(sp)) {
                const positions = loadJsonSafe(sp);
                data[bot].short.push(...positions);
            }
        }
    }
    res.json(data);
});

app.get('/api/market', (req: Request, res: Response) => {
    const marketData: any = {};
    const paths = [
        path.join(PROJECT_ROOT, "market_data.json"),
        path.join(DATA_DIR, "market_data.json"),
        path.join(PROJECT_ROOT, "indicators.json"),
        path.join(DATA_DIR, "indicators.json"),
        path.join(BINANCE_DIR, "market_data.json"),
        path.join(NIELS_BINANCE, "market_data.json")
    ];

    for (const baseDir of [BINANCE_DIR, PROJECT_ROOT, NIELS_BINANCE]) {
        for (const bot of ALL_BOTS) {
            const botDir = path.join(baseDir, bot);
            paths.push(path.join(botDir, "market_data.json"));
            paths.push(path.join(botDir, "indicators.json"));
        }
    }

    for (const p of paths) {
        if (fs.existsSync(p)) {
            try {
                const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
                Object.assign(marketData, data);
            } catch (e) {}
        }
    }
    res.json(marketData);
});

app.get('/api/logs', async (req: Request, res: Response) => {
    const result = { stock: '', crypto: '' };
    const logDirs = [LOGS_DIR, PROJECT_ROOT, NIELS_LOGS, DECISIONS_DIR];
    
    const logFiles = ["actions.log", "tradier.log", "crypto_actions.log", "tradier_actions.log"];
    
    for (const dir of logDirs) {
        if (!fs.existsSync(dir)) continue;
        
        // Root logs in this dir
        for (const file of logFiles) {
            const p = path.join(dir, file);
            if (fs.existsSync(p)) {
                try {
                    const content = fs.readFileSync(p, 'utf-8');
                    const cat = file.includes('tradier') ? 'stock' : 'crypto';
                    result[cat] += content + "\n";
                } catch (e) {}
            }
        }

        // Handle JSONL decisions if in DECISIONS_DIR or any dir
        try {
            const jsonlFiles = await glob(path.join(dir, "*.jsonl"));
            for (const fpath of jsonlFiles) {
                try {
                    const content = fs.readFileSync(fpath, 'utf-8');
                    result.crypto += content + "\n";
                } catch (e) {}
            }
        } catch (e) {}
        
        // Bot subfolder logs
        for (const bot of ALL_BOTS) {
            const botDir = path.join(dir, bot);
            if (fs.existsSync(botDir)) {
                for (const file of logFiles) {
                    const p = path.join(botDir, file);
                    if (fs.existsSync(p)) {
                        try {
                            const content = fs.readFileSync(p, 'utf-8');
                            const cat = file.includes('tradier') ? 'stock' : 'crypto';
                            result[cat] += content + "\n";
                        } catch (e) {}
                    }
                }
            }
        }
    }
    
    res.json(result);
});

// Vite middleware for development
async function startServer() {
    if (process.env.NODE_ENV !== "production") {
        const vite = await createViteServer({
            server: { middlewareMode: true },
            appType: "spa",
        });
        app.use(vite.middlewares);
    } else {
        const distPath = path.join(__dirname, "dist");
        if (fs.existsSync(distPath)) {
            app.use(express.static(distPath));
            app.get("*", (req: Request, res: Response) => {
                res.sendFile(path.join(distPath, "index.html"));
            });
        }
    }

    app.listen(PORT, "0.0.0.0", () => {
        console.log(`Server running on http://localhost:${PORT}`);
    });
}

startServer();
