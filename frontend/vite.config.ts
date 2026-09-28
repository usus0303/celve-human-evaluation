import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';
export default defineConfig({base:'/static/',plugins:[react()],resolve:{alias:{'@':path.resolve(import.meta.dirname,'src')}},build:{outDir:'../static',emptyOutDir:false},server:{proxy:{'/api':'http://127.0.0.1:8000','/media':'http://127.0.0.1:8000'}}});
