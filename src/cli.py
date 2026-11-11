import uvicorn
import qrcode
from rich.console import Console
from rich.panel import Panel
from src.network import get_local_ip

console = Console()

def main():
    ip = get_local_ip()
    port = 8000
    url = f"http://{ip}:{port}"
    
    # Generate QR Code
    qr = qrcode.QRCode()
    qr.add_data(url)
    
    console.print("\n")
    console.print(Panel(f"[bold green]Drop-Flow запущен![/bold green]\n\nЛокальный адрес: [cyan]{url}[/cyan]\nОтсканируй QR-код телефоном, чтобы подключиться:", expand=False))
    
    qr.print_ascii(tty=True)
    
    uvicorn.run("src.server:app", host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    main()
