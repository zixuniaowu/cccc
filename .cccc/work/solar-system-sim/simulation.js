class SolarSystemSimulation {
    constructor() {
        this.canvas = document.getElementById('solarSystem');
        this.ctx = this.canvas.getContext('2d');
        this.isPlaying = false;
        this.speed = 1;
        this.zoom = 1;
        this.time = 0;
        this.selectedPlanet = null;
        
        this.centerX = 0;
        this.centerY = 0;
        this.dragStart = null;
        this.cameraOffset = { x: 0, y: 0 };
        
        this.setupCanvas();
        this.setupControls();
        this.setupInteraction();
        this.animate();
    }
    
    setupCanvas() {
        const resize = () => {
            this.canvas.width = this.canvas.clientWidth;
            this.canvas.height = this.canvas.clientHeight;
            this.centerX = this.canvas.width / 2;
            this.centerY = this.canvas.height / 2;
        };
        resize();
        window.addEventListener('resize', resize);
    }
    
    setupControls() {
        const playPauseBtn = document.getElementById('playPause');
        const speedControl = document.getElementById('speedControl');
        const zoomControl = document.getElementById('zoomControl');
        const resetBtn = document.getElementById('resetView');
        
        playPauseBtn.addEventListener('click', () => {
            this.isPlaying = !this.isPlaying;
            playPauseBtn.textContent = this.isPlaying ? '⏸️ Pause' : '▶️ Play';
        });
        
        speedControl.addEventListener('input', (e) => {
            this.speed = parseFloat(e.target.value);
        });
        
        zoomControl.addEventListener('input', (e) => {
            this.zoom = parseFloat(e.target.value);
        });
        
        resetBtn.addEventListener('click', () => {
            this.zoom = 1;
            this.speed = 1;
            this.cameraOffset = { x: 0, y: 0 };
            speedControl.value = 1;
            zoomControl.value = 1;
        });
    }
    
    setupInteraction() {
        this.canvas.addEventListener('mousedown', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            const planet = this.getPlanetAtPosition(x, y);
            if (planet) {
                this.selectPlanet(planet);
            } else {
                this.dragStart = { x: e.clientX, y: e.clientY };
                this.canvas.style.cursor = 'grabbing';
            }
        });
        
        this.canvas.addEventListener('mousemove', (e) => {
            if (this.dragStart) {
                const dx = e.clientX - this.dragStart.x;
                const dy = e.clientY - this.dragStart.y;
                this.cameraOffset.x += dx;
                this.cameraOffset.y += dy;
                this.dragStart = { x: e.clientX, y: e.clientY };
            }
        });
        
        this.canvas.addEventListener('mouseup', () => {
            this.dragStart = null;
            this.canvas.style.cursor = 'grab';
        });
        
        this.canvas.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? 0.9 : 1.1;
            this.zoom = Math.max(0.5, Math.min(3, this.zoom * delta));
            document.getElementById('zoomControl').value = this.zoom;
        });
    }
    
    getPlanetAtPosition(x, y) {
        const adjustedX = x - this.centerX - this.cameraOffset.x;
        const adjustedY = y - this.centerY - this.cameraOffset.y;
        
        for (const planet of PLANETS) {
            const angle = (this.time * 0.001 / planet.orbitalPeriod) % (2 * Math.PI);
            const planetX = Math.cos(angle) * planet.distance * this.zoom;
            const planetY = Math.sin(angle) * planet.distance * this.zoom;
            
            const distance = Math.sqrt(
                Math.pow(adjustedX - planetX, 2) + 
                Math.pow(adjustedY - planetY, 2)
            );
            
            if (distance <= planet.radius * this.zoom) {
                return planet;
            }
        }
        return null;
    }
    
    selectPlanet(planet) {
        this.selectedPlanet = planet;
        const infoPanel = document.getElementById('planetInfo');
        infoPanel.innerHTML = `
            <strong>${planet.name}</strong><br>
            Orbital Period: ${planet.orbitalPeriod} Earth years<br>
            Day Length: ${Math.abs(planet.rotationPeriod)} Earth days<br>
            ${planet.info}
        `;
    }
    
    drawSun() {
        const x = this.centerX + this.cameraOffset.x;
        const y = this.centerY + this.cameraOffset.y;
        
        if (SUN.glow) {
            const gradient = this.ctx.createRadialGradient(x, y, 0, x, y, SUN.radius * this.zoom * 2);
            gradient.addColorStop(0, 'rgba(255, 215, 0, 0.8)');
            gradient.addColorStop(0.5, 'rgba(255, 215, 0, 0.3)');
            gradient.addColorStop(1, 'rgba(255, 215, 0, 0)');
            this.ctx.fillStyle = gradient;
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        }
        
        this.ctx.fillStyle = SUN.color;
        this.ctx.beginPath();
        this.ctx.arc(x, y, SUN.radius * this.zoom, 0, Math.PI * 2);
        this.ctx.fill();
    }
    
    drawPlanet(planet) {
        const angle = (this.time * 0.001 / planet.orbitalPeriod) % (2 * Math.PI);
        const x = this.centerX + Math.cos(angle) * planet.distance * this.zoom + this.cameraOffset.x;
        const y = this.centerY + Math.sin(angle) * planet.distance * this.zoom + this.cameraOffset.y;
        
        // Draw orbit
        this.ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
        this.ctx.beginPath();
        this.ctx.arc(
            this.centerX + this.cameraOffset.x,
            this.centerY + this.cameraOffset.y,
            planet.distance * this.zoom,
            0,
            Math.PI * 2
        );
        this.ctx.stroke();
        
        // Draw planet
        this.ctx.fillStyle = planet.color;
        this.ctx.beginPath();
        this.ctx.arc(x, y, planet.radius * this.zoom, 0, Math.PI * 2);
        this.ctx.fill();
        
        // Draw selection indicator
        if (this.selectedPlanet === planet) {
            this.ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
            this.ctx.lineWidth = 2;
            this.ctx.beginPath();
            this.ctx.arc(x, y, (planet.radius + 5) * this.zoom, 0, Math.PI * 2);
            this.ctx.stroke();
        }
        
        // Draw label
        this.ctx.fillStyle = 'white';
        this.ctx.font = '12px Arial';
        this.ctx.textAlign = 'center';
        this.ctx.fillText(planet.name, x, y - (planet.radius + 10) * this.zoom);
    }
    
    animate() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        this.drawSun();
        PLANETS.forEach(planet => this.drawPlanet(planet));
        
        if (this.isPlaying) {
            this.time += 16 * this.speed;
        }
        
        requestAnimationFrame(() => this.animate());
    }
}

// Initialize simulation when page loads
window.addEventListener('DOMContentLoaded', () => {
    new SolarSystemSimulation();
});