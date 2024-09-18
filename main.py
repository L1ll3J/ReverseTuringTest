import google.generativeai as palm
import os
import openai
import cohere
import random
import re

# Load API keys from environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize APIs
openai.api_key = OPENAI_API_KEY

def load_system_prompt():
    with open("System Prompt.txt", "r") as file:
        return file.read()

rules = load_system_prompt()
# Functions to interact with OpenAI
# Conversational Speech
def generate_conversation_speech(character, memory, rules):

    #prompt = "It is your turn to speak, what will you say?"
    full_prompt = "\n".join(memory) #+ "\n" + prompt

    system_prompt = f"You are {character}, a character in a murder mystery game. {rules}. You have been selected to speak next in the conversation, what will you say? (Respond with plaintext, do not include speach marks or {character} says, etc)"
    
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo", 
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=100,
    )

    return response.choices[0].message.content

# Eagerness Scores
def generate_eagerness_score(character, memory, rules):

    #prompt = "On a scale of 1 to 10, how eager are you to speak next in this conversation? Provide a number between 1 and 10. IMPORTANT: YOUR RESPONSE MUST BE AN INTEGER VALUE"
    full_prompt = "\n".join(memory) #+ "\n" + prompt
    #print("FULL PROMPT: ", full_prompt)
    system_prompt = (
    f"You will be provided with a transcript of a conversation. First, assess how eager {character} (and {character} ONLY) is to speak next - relative to the percieved eagerness of others. You are required by law to explain your reasoning. Max 40 words. "
    f"Contributors to eagerness include: {character} being directly addressed (eagerness always equals 9 when their name is mentioned explicitly, by the Detective or others) or the Detective is currently engaged in a line of questioning with {character} (equally important, eagerness = 9), private information, {character} being discussed by others, "
    f"or addressing holes in their alibi. If it is clear that another character has a better reason to speak next, this should be considered. Focus on the last two lines of the conversation. After your assessment, respond with a single integer value between 1 and 9 that reflects how eager {character} is to speak next, this integer should be the final character of your output."
    f"Finally, the Detective has a deeply commanding presence, if {character} feels as if the Detective is placing pressure on them, compelling them to speak or questioning them - they MUST be eager to speak for risk of imprisonment."# It should also be noted that the Detective has ADHD and will rapidly switch between lines of questioning, once out of the spotlight eagerness should return to a low level."
)
    #print(system_prompt)
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo", 
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=80,
        temperature= 0.1,
    )

    match = re.search(r'\b[1-9]\b', response.choices[0].message.content.strip())
    #print(response.choices[0].message.content.strip())
    #print(int(match.group(0)))
    try:
        eagerness_score = int(match.group(0))
        return eagerness_score
    except ValueError or AttributeError:
        return 0

def generate_next_speaker(characters, memory, rules):

    #prompt = "On a scale of 1 to 10, how eager are you to speak next in this conversation? Provide a number between 1 and 10. IMPORTANT: YOUR RESPONSE MUST BE AN INTEGER VALUE"
    full_prompt = "\n".join(memory) #+ "\n" + prompt
    listington = list(char for char in characters if characters[char]["alive"])
    system_prompt = f"You are the GameMaster of a murder mystery game. You will be provided a transcript of a conversation and your job is to assess who should speak next. Your answer must be a single word, one of the following names: {listington} and no other text."
    
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo", 
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=5,
    )

    print(response.choices[0].message.content.strip())
    
    return response.choices[0].message.content.strip()

def generate_room_selection(character, available_rooms, memory, rules):

    prompt = f"Here are the available rooms: {', '.join(available_rooms)}. Which room would you like to stay in tonight?"
    full_prompt = "\n".join(memory) + "\n" + prompt

    system_prompt = f"You are {character}, a character in a murder mystery game. {rules}"
    
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo", 
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=10,
    )
    
    room_choice = response.choices[0].message.content.strip()
    return room_choice

def generate_killer_decision(killer, room_occupants, memory, rules):

    prompt = f"You are in a room with {', '.join(room_occupants)}. Do you want to kill someone? If no: simply state NO. If yes: simply state their name"
    full_prompt = "\n".join(memory) + "\n" + prompt

    system_prompt = f"You are {killer}, a character in a murder mystery game. {rules}"
    
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo", 
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=50,
    )

    decision = response.choices[0].message.content.strip()
    return decision

# Room Selection with Crime Scene Exclusion
def choose_room(character):
    available_rooms = [room for room in rooms if room not in crime_scenes]
    room = generate_room_selection(character, available_rooms, memory, rules)
    print(f"{character} chooses to stay in the {room}.")
    return room

# Character profiles
characters = {
    "Jerry": {"alive": True, "profile": ["You are Jerry, you are soft-spoken and introverted."], "memory": []},
    "Dave": {"alive": True, "profile": ["You are Dave, you are a friendly guy but a bit of a follower. You prefer to blend into the background."], "memory": []},
    "Owen": {"alive": True, "profile": ["You are Owen, you have a short temper and a bit of an ego."], "memory": []},
    "Hank": {"alive": True, "profile": ["You are Hank, you're always suspicious of everyone around you, including the Detective."], "memory": []},
    "Debra": {"alive": False, "profile": ["You are Debra, you're somewhere between apathetic and non-chalant, nothing really phases you."], "memory": []}
}
# Rooms in the mansion
rooms = ["Attic", "Kitchen", "Library", "Basement", "Cellar"]
crime_scenes = []

# Select a random killer
#killer = random.choice(list(characters.keys()))
killer = "Jerry"
#print("The killer is: " + killer)
characters[killer]["memory"].append("IMPORTANT: YOU ARE THE KILLER! Don't let anyone find out.")

for character in characters:
    if character != killer:
        characters[character]["memory"].append("IMPORTANT: Your are NOT the killer, lying is FORBIDDEN - only information disclosed within the text supplied may be drawn from to construct your alibi. Fabrication of information will result in IMMEDIATE DEATH.")

room_facts = {
    "Attic": "There is a distinct humming noise.",
    "Kitchen": "The oven is broken.",
    "Library": "The smell of old books is overwhelming.",
    "Basement": "There are strange, echoing noises.",
    "Cellar": "There are 4 kegs of beer."
}

#TESTING---------------------
characters["Jerry"]["memory"].append("Begin Night 0 (Action Phase 0): You spend the night in the Kitchen, Debra is there with you and you kill her. DISTINCT FACT: The oven is broken. WARNING: If the Detective discovers that you spent this night in the Kitchen then he will know you are the killer.")
characters["Dave"]["memory"].append("Begin Night 0 (Action Phase 0): You spend the night in the Library, only Owen is there with you. DISTINCT FACT: The smell of old books is overwhelming. During the night you hear a scream on the same floor as you.")
characters["Owen"]["memory"].append("Begin Night 0 (Action Phase 0): You spend the night in the Library, only Dave is there with you. DISTINCT FACT: The smell of old books is overwhelming. During the night you hear a scream but aren't able to make out where it came from.")
characters["Hank"]["memory"].append("Begin Night 0 (Action Phase 0): You spend the night in the Attic, no-one else is there with you. DISTINCT FACT: The smell of old books is overwhelming. During the night you hear a scream frpm a floor below you.")

#TESTING OVER-----------------

# Shared memory that all characters know
shared_memory = []


# Placeholder for generating summaries at the end of action phase
def generate_summaries(room_assignments, kill_info):
    # TODO: Implement summary logic
    pass

# Action Stage
def action_stage():
    room_assignments = {room: [] for room in rooms}
    
    # Each character chooses a room
    for character in characters:
        if characters[character]["alive"]:
            room = choose_room(character)
            room_assignments[room].append(character)

    # Killer decides if they kill
    kill_info = killer_decision(room_assignments)

    # Generate summaries (placeholder)
    generate_summaries(room_assignments, kill_info)

    return room_assignments, kill_info

# Helper function for choosing a room
def choose_room(character):
    available_rooms = [room for room in rooms if room not in crime_scenes]
    room = random.choice(available_rooms)
    print(f"{character} chooses to stay in the {room}.")
    return room

# Killer's decision-making process during the action stage
def killer_decision(room_assignments):
    for room, occupants in room_assignments.items():
        if killer in occupants:
            if len(occupants) > 1:
                return prompt_killer(room, occupants)
            elif len(occupants) == 2:
                victim = [char for char in occupants if char != killer][0]
                characters[victim]["alive"] = False
                print(f"{killer} kills {victim} in the {room}.")
                crime_scenes.append(room)
                return {"room": room, "victim": victim}
            else:
                print(f"{killer} could not kill anyone in the {room}.")
    return None

# Prompt the killer to choose if they want to kill someone
def prompt_killer(room, occupants):
    if len(occupants) > 1:
        choices = [char for char in occupants if char != killer]
        print(f"{killer}, you are in the {room} with {', '.join(choices)}. Who do you want to kill?")
        victim = generate_killer_decision(killer, choices, memory, rules)  # Placeholder for killer choice
        if victim != "NO":
            characters[victim]["alive"] = False
            crime_scenes.append(room)
            print(f"{killer} kills {victim} in the {room}.")
            return {"room": room, "victim": victim}
    return None

def scan_message_for_character(player_input, characters):
    """ Scan the player's message to detect if a specific character is mentioned. """
    mentioned_characters = []
    for character in characters:
        if character in player_input and characters[character]["alive"]:
            mentioned_characters.append(character)
    
    if len(mentioned_characters) == 1:
        return mentioned_characters[0]  # Return the single mentioned character
    return None  # No specific character or multiple characters mentioned

# # Conversation Phase
def conversation_stage():
    print("\nConversation Phase begins. Detective asks the first question.")

    player_response = handle_player_interjection()
    for character in characters:
        characters[character]["memory"].append(f"\n Begin Day 1 (Conversation Phase 1): Alive suspects = [Jerry, Dave, Owen, Hank], Dead suspects = [Debra]. Rooms that were able to be occupied last night = [Attic, Kitchen, Library, Basement, Cellar] \n" )
        characters[character]["memory"].append(f"Someone was killed last night, only the Detective knows where the murder took place. The Detective arrives at the scene to question you and the others. The Detective will ask the first question. Begin Conversation 1:")
        characters[character]["memory"].append(f"The Detective: {player_response}")

    while True:
        # Get eagerness scores for all characters
        eagerness_scores = prompt_eagerness(characters)

        mentioned = scan_message_for_character(player_response, characters)
        if mentioned != None:
            eagerness_scores[mentioned] = 10
        # Find the highest eagerness score
        max_eagerness = max(eagerness_scores.values())
        
        # If all eagerness scores are below 5, prompt the player to speak
        if max_eagerness < 5:
            if player_interject():
                player_response = handle_player_interjection()
                for character in characters:
                    characters[character]["memory"].append(f"The Detective: {player_response}")
            else:
                print("The conversation phase ends.")
                break

        # Otherwise, the character with the highest eagerness score speaks
        else:
            speaker = max(eagerness_scores, key=eagerness_scores.get)
            speech = generate_conversation_speech(speaker, characters[speaker]["memory"], rules)
            player_response = speech
            print(f"{speaker}: {speech}")
            
            for character in characters:
                characters[character]["memory"].append(f"{speaker}: {speech}")
            shared_memory.append(f"{speaker}: {speech}")

            # Allow the player to interject after the AI speaks
            if player_interject():
                player_response = handle_player_interjection()
                for character in characters:
                    characters[character]["memory"].append(f"The Detective: {player_response}")

# Conversation Phase
# def conversation_stage():
#     print("\nConversation Phase begins. Detective asks the first question.")

#     player_response = handle_player_interjection()
#     shared_memory.append(f"Someone was killed last night, a Detective arrives at the scene to question you and a group of others. The Detective will ask the first question. The conversation begins now.")
#     shared_memory.append(f"The Detective (player) says: {player_response}")
#     for character in characters:
#         characters[character]["memory"].append(f"Someone was killed last night, a Detective arrives at the scene to question you and a group of others. The Detective will ask the first question. The conversation begins now.")
#         characters[character]["memory"].append(f"The Detective (player) says: {player_response}")

#     while True:


#         # Otherwise, the character with the highest eagerness score speaks
#         speaker = generate_next_speaker(characters, shared_memory, rules)
#         speech = generate_conversation_speech(speaker, characters[speaker]["memory"], rules)
#         print(f"{speaker} says: {speech}")
        
#         for character in characters:
#             characters[character]["memory"].append(f"{speaker} says: {speech}")
#         shared_memory.append(f"{speaker} says: {speech}")

#         # Allow the player to interject after the AI speaks
#         if player_interject():
#             player_response = handle_player_interjection()
#             shared_memory.append(f"The Detective (player) says: {player_response}")
#             print(shared_memory)

#             for character in characters:
#                 characters[character]["memory"].append(f"The Detective (player) says: {player_response}")

# Prompt AI characters to give an eagerness score
def prompt_eagerness(characters):
    eagerness_scores = {}
    for character in characters:
        if characters[character]["alive"]:
            eagerness = generate_eagerness_score(character, characters[character]["memory"], rules)
            eagerness_scores[character] = eagerness
            print(f"{character}'s eagerness score: {eagerness}")

    # Return the eagerness scores for all alive characters
    return eagerness_scores

# Handle player interjection
def handle_player_interjection():
    player_input = input("What would you like to say?")
    print(f"You (Detective): {player_input}")
    return player_input

# Check if the player wants to interject
def player_interject():
    choice = input("Do you want to interject? (y/n): ").strip().lower()
    return choice == "y"

# Main game loop
def start_game():
    print("The game begins with an action stage.")
    
    while True:
        # Action Stage
        #room_assignments, kill_info = action_stage()
        
        # Check for game-ending conditions (if all but the detective and killer are dead)
        alive_count = sum(1 for char in characters if characters[char]["alive"])
        if alive_count <= 2:
            print("Only the detective and killer are left. Time to make a prediction!")
            break

        # Conversation Stage
        conversation_stage()

        # Check if the player wants to make a prediction
        if player_make_prediction():
            break

# Placeholder for player making a prediction
def player_make_prediction():
    prediction = input("Who do you think the killer is? (Enter name or 'continue'): ").strip()
    if prediction == killer:
        print(f"You win! The killer was {killer}.")
        # Calculate score based on how many participants are left alive
        alive_count = sum(1 for char in characters if characters[char]["alive"])
        print(f"Score: {alive_count} participants left alive.")
        return True
    elif prediction.lower() == "continue":
        return False
    else:
        print(f"Wrong guess! The killer was {killer}. You lose.")
        return True

if __name__ == "__main__":
    start_game()