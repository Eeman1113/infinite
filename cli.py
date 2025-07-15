import ollama
import random
import time
import re
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.status import Status
from rich.layout import Layout
from rich.align import Align
import asyncio # Import asyncio for async operations

# Initialize Rich Console
console = Console()

# --- Game State Variables ---
discovered_elements = {}  # Stores elements as {name: {"emoji": "🔥", "id": 0}}
next_element_id = 0
combinations_made = set() # To store unique combinations to avoid redundant API calls
# Store combinations as frozenset of element names to ensure order-independence
# e.g., frozenset({"Fire", "Water"})

# Global variables for agent status, managed by update_agent_status
agent_status_message = "Initializing..."
show_spinner = False

# List to store formatted log messages for display in the "Crafting Log" panel
# Each entry will be a rich.text.Text object
formatted_log_lines = []

# --- Ollama Configuration ---
OLLAMA_MODEL = 'gemma3:12b' # Using the largest model provided by the user
OLLAMA_HOST = 'http://localhost:11434' # Default Ollama host

# Initialize Ollama client with the specified host
ollama_client = ollama.Client(host=OLLAMA_HOST)

# --- Helper Functions ---

def update_agent_status(status_text: str, spinner: bool = True):
    """Updates the agent status message."""
    global agent_status_message
    global show_spinner
    agent_status_message = status_text
    show_spinner = spinner

def add_element(name: str, emoji: str):
    """Adds a new element to the game state if it doesn't already exist."""
    global next_element_id
    if name not in discovered_elements:
        discovered_elements[name] = {"emoji": emoji, "id": next_element_id}
        next_element_id += 1
        return True
    return False # Element already exists

def get_element_display_name(element_name: str) -> str:
    """Returns the formatted display name for an element."""
    element_info = discovered_elements.get(element_name)
    if element_info:
        return f"{element_info['emoji']} {element_name}"
    return element_name # Fallback if element not found (shouldn't happen for discovered)

def add_log_entry(log_type: str, element1: str = None, element2: str = None, result: str = None, message: str = None):
    """
    Adds a structured log entry to be formatted and displayed in the crafting log.
    log_type: "discovery", "re-discovery", "error", "info"
    """
    # Calculate max log lines based on console height to prevent overflow
    # Subtracting extra lines for header, footer, and panel borders/padding
    max_log_lines = console.height - 15 

    log_text = Text()
    if log_type == "discovery":
        # Format for new discoveries: "Element1 + Element2 => NewResult (New!)" in bold green/blue
        e1_display = get_element_display_name(element1)
        e2_display = get_element_display_name(element2)
        result_display = get_element_display_name(result)
        log_text.append(f"{e1_display} ", style="bold blue")
        log_text.append("+", style="white")
        log_text.append(f" {e2_display} ", style="bold blue")
        log_text.append("=>", style="white")
        log_text.append(f" {result_display}", style="bold green")
        log_text.append(" (New!)", style="green italic")
    elif log_type == "re-discovery":
        # Format for re-discoveries: "Element1 + Element2 => ExistingResult (Re-discovered)" in dimmed yellow/blue
        e1_display = get_element_display_name(element1)
        e2_display = get_element_display_name(element2)
        result_display = get_element_display_name(result)
        log_text.append(f"{e1_display} ", style="dim blue")
        log_text.append("+", style="dim white")
        log_text.append(f" {e2_display} ", style="dim blue")
        log_text.append("=>", style="dim white")
        log_text.append(f" {result_display}", style="dim yellow")
        log_text.append(" (Re-discovered)", style="yellow italic dim")
    elif log_type == "error":
        # Format for errors in bold red
        log_text.append(f"[ERROR] {message}", style="bold red")
    elif log_type == "info":
        # Format for general information messages in dimmed white
        log_text.append(message, style="dim white")

    formatted_log_lines.append(log_text)
    
    # Keep only the latest log entries to fit the display area
    if len(formatted_log_lines) > max_log_lines:
        formatted_log_lines.pop(0) # Remove the oldest message

def create_game_layout(elements_panel, status_panel):
    """Creates the Rich Layout for the game display."""
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=5)
    )
    layout["main"].split_row(
        Layout(name="elements_list"),
        Layout(name="combinations_graph") # Placeholder for graph visualization (not implemented in CLI)
    )

    layout["header"].update(
        Align.center(
            Text("✨ Infinite Craft (CLI Edition) ✨", style="bold magenta on black", justify="center"),
            vertical="middle"
        )
    )
    layout["elements_list"].update(elements_panel)
    
    # Display the captured log messages in the combinations_graph panel
    # Join the rich.text.Text objects with newlines for display
    log_content = Text("\n").join(formatted_log_lines)
    layout["combinations_graph"].update(
        Panel(
            log_content,
            title="[bold green]Crafting Log[/bold green]",
            border_style="green",
            height=elements_panel.height # Match height of elements panel for consistent layout
        )
    )
    layout["footer"].update(status_panel)
    return layout

def generate_elements_panel():
    """Generates the Rich Panel for the discovered elements list."""
    table = Table(box=None, show_header=False, show_lines=False, padding=0)
    table.add_column("Elements", justify="left", style="white")

    sorted_elements = sorted(discovered_elements.keys())
    for name in sorted_elements:
        emoji = discovered_elements[name]["emoji"]
        table.add_row(f"[bold]{emoji}[/bold] {name}")

    return Panel(
        table,
        title=f"[bold cyan]Discovered Elements ({len(discovered_elements)})[/bold cyan]",
        border_style="cyan",
        height=console.height - 10 # Adjust height dynamically based on console size
    )

def generate_status_panel(status_message: str, show_spinner: bool):
    """Generates the Rich Panel for the agent status."""
    status_text = Text(status_message, style="italic yellow" if show_spinner else "bold green")
    if show_spinner:
        # Rich's Status object handles the spinner animation
        return Panel(
            Status(status_text, spinner="dots", spinner_style="yellow"),
            title="[bold blue]Agent Status[/bold blue]",
            border_style="blue",
            height=3
        )
    else:
        return Panel(
            Align.center(status_text, vertical="middle"),
            title="[bold blue]Agent Status[/bold blue]",
            border_style="blue",
            height=3
        )

async def combine_elements_with_ai(element1_name: str, element2_name: str):
    """
    Calls the Ollama API to combine two elements and generate a new one.
    Returns a dictionary {name: str, emoji: str} or None on failure.
    """
    update_agent_status(f"Combining: {element1_name} + {element2_name}...", spinner=True)

    prompt = f"""You are an AI that creates new elements by combining two existing elements.
Combine "{element1_name}" and "{element2_name}".
The new element MUST be a plausible, real-world concept or a commonly understood combination.
Your response MUST ONLY contain the new element's name, followed by a single, highly relevant emoji.
Do NOT create fictional, abstract, or overly fantastical elements.
Do NOT include any other text, punctuation, explanations, or conversational filler.
Example format: "NewElement 🌟"
Ensure the emoji is directly related to the new element."""

    try:
        # Use the initialized ollama_client instead of direct ollama.generate()
        response = ollama_client.generate(
            model=OLLAMA_MODEL,
            prompt=prompt,
            options={
                'temperature': 0.7,
                'top_k': 40,
                'top_p': 0.9,
            },
            stream=False,
        )

        if 'response' in response:
            text = response['response'].strip()
            
            # Split the response to separate name and emoji
            parts = text.rsplit(' ', 1) # Split from right, at most once
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                name = parts[0].strip()
                emoji = parts[1].strip()
                
                # Basic check to ensure the second part is likely an emoji
                if any(ord(char) > 127 for char in emoji) and not re.search(r'[a-zA-Z0-9]', emoji):
                    update_agent_status(f"Discovered: {name} {emoji}", spinner=False)
                    return {"name": name, "emoji": emoji}
                else:
                    update_agent_status(f"AI response format error: {text} (Emoji part invalid)", spinner=False)
                    add_log_entry("error", message=f"AI response format error: Expected 'Name Emoji', got: '{text}' (Invalid emoji part)")
                    return None
            else:
                update_agent_status(f"AI response format error: {text}", spinner=False)
                add_log_entry("error", message=f"AI response format error: Expected 'Name Emoji', got: '{text}'")
                return None
        else:
            update_agent_status("Ollama did not return a valid response.", spinner=False)
            add_log_entry("error", message=f"Ollama did not return a valid response: {response}")
            return None
    except ollama.ResponseError as e:
        update_agent_status(f"Ollama API Error: {e}", spinner=False)
        add_log_entry("error", message=f"Ollama API Error: {e}")
        return None
    except Exception as e:
        update_agent_status(f"Network/Other Error: {e}", spinner=False)
        add_log_entry("error", message=f"Error calling Ollama: {e}")
        return None

async def agent_single_combination_attempt():
    """Performs a single combination attempt and updates game state."""
    if len(discovered_elements) < 2:
        update_agent_status("Waiting for more elements...", spinner=False)
        return

    elements_list = list(discovered_elements.keys())
    
    element1_name, element2_name = None, None
    
    # Try to find a new combination
    attempts = 0
    max_attempts = 50 # Prevent infinite loops if all combinations are exhausted
    while attempts < max_attempts:
        element1_name = random.choice(elements_list)
        element2_name = random.choice(elements_list)

        # Ensure elements are different
        if element1_name == element2_name:
            attempts += 1
            continue

        # Ensure combination hasn't been tried before (order-independent)
        current_combination = frozenset({element1_name, element2_name})
        if current_combination not in combinations_made:
            combinations_made.add(current_combination)
            break
        attempts += 1
    else:
        update_agent_status("All known combinations exhausted or no new pairs found!", spinner=False)
        return # No new combination found, return

    new_element_data = await combine_elements_with_ai(element1_name, element2_name)

    if new_element_data:
        # Check if the element is truly new or a re-discovery
        if add_element(new_element_data["name"], new_element_data["emoji"]):
            add_log_entry("discovery", element1=element1_name, element2=element2_name, result=new_element_data["name"])
        else:
            add_log_entry("re-discovery", element1=element1_name, element2=element2_name, result=new_element_data["name"])
    
    # After a combination attempt, wait before the next one to control API call rate
    await asyncio.sleep(7) 

async def main():
    """Initializes the game and starts the agent loop."""
    global agent_status_message
    global show_spinner

    console.clear() # Clear console for a clean start
    console.print(Panel(Text("Starting Infinite Craft...", justify="center", style="bold yellow"), border_style="yellow"))
    await asyncio.sleep(1) # Short delay for initial message

    # Add initial elements
    add_element("Fire", "🔥")
    add_element("Water", "💧")
    add_element("Earth", "🌍")
    add_element("Wind", "🌬️")
    
    # Log the addition of initial elements
    add_log_entry("info", message="Initial elements added: Fire, Water, Earth, Wind.")

    agent_status_message = "Game started! Initial elements added."
    show_spinner = False

    # Use Rich's Live context manager to continuously update the console display
    with Live(screen=True, refresh_per_second=4) as live:
        while True:
            # Generate and update the UI panels
            elements_panel = generate_elements_panel()
            status_panel = generate_status_panel(agent_status_message, show_spinner)
            game_layout = create_game_layout(elements_panel, status_panel)
            live.update(game_layout)
            
            # Perform a single combination attempt by the AI agent
            await agent_single_combination_attempt() 

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[bold red]Game stopped by user.[/bold red]")
    except Exception as e:
        console.print(f"\n[bold red]An unexpected error occurred:[/bold red] {e}")

