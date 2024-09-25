import google.generativeai as palm
import os
import openai
import cohere
import random
import re
from collections import Counter

# Load API keys from environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize API
openai.api_key = OPENAI_API_KEY

def load_system_prompt():
    with open("System Prompt.txt", "r") as file:
        return file.read()

rules = load_system_prompt()

# # Functions to interact with OpenAI
# Conversational Speech from a single character
def generate_conversation_speech(character, characters, rules, current_context, most_recent):

    #prompt = "It is your turn to speak, what will you say?"
    full_prompt = "\n".join(characters[character]["memory"]) + "\n" + "Current conversational context:" + "\n" + current_context + "Most recent message(s):" + "\n" + "\n".join(most_recent)

    system_prompt = f"You are {character}, a character in an Among-Us style murder mystery game. \n {rules}. You have been selected to speak next in the conversation, what will you say? (Respond with plaintext, do not include speach marks or {character} says, etc). \n Remember, you are {character} and your output is what they will say next in the conversation, if you do not believe it is {character}'s turn to speak next, simply state you have nothing to say."
    
    response = openai.chat.completions.create(
        model="gpt-4o-mini", 
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        max_tokens=100,
    )

    return response.choices[0].message.content.split(":")[-1]

# Generate response from each possible (alive) character
def generate_responses(characters, rules, conversation_context, most_recent):
    """Generate a response from each AI character based on the conversation context."""
    responses = {}
    for character in characters:
        if characters[character]["alive"]:
            response = generate_conversation_speech(character, characters, rules, conversation_context, most_recent)
            responses[character] = response
    return responses


# Select which response fits best into the conversation to decide which character speaks next
def select_best_response(responses, conversation_context, most_recent, NPCs):
    response_summary = "\n".join([f"{char}: {resp}" for char, resp in responses.items()])
    
    overseer_prompt = (
        f"The following responses were generated by the characters in a murder mystery conversation. "
        f"Choose the most fitting response based on the current conversation context:\n\n{conversation_context}\n\n"
        f"The most recent messages: \n{most_recent}\n"
        f"Responses:\n{response_summary}\n\n"
        f"First priority should be placed on a reply that calls out a contradiction or makes a direct accusation."
        f"Second priority should be given to a character that has not yet had a chance to establish their alibi and is doing so currently."
        f"Third priority should be given to a character asking a question of another's alibi"
        f"Ensure that the conversation does not loop around in circles or make scarce progress, ensure all characters get to have their say at some point."
        f"Provide the name of the character whose response best fits the conversation. (MUST be one of {NPCs})"
    )

    response = openai.chat.completions.create(
        model="gpt-4o-mini", 
        messages=[
            {"role": "system", "content": f"You are an overseer managing the flow of conversation in a murder mystery game. Your answer must be a single word."},
            {"role": "user", "content": overseer_prompt}
        ],
        max_tokens=5,
    )
    speaker = response.choices[0].message.content.strip().rstrip(".").rstrip(",")

    if speaker == "Josh":
        print(NPCs)
    if speaker not in NPCs:
        print("ERROR: Couldn't select best response, responsdant chosen randomly")
        speaker = random.choice(NPCs)

    best_response = responses[speaker].replace("{", "").replace("}", "")
    return best_response, speaker

# Summarise conversation to reduce complexity, using entire conversation transcripts quickly overloads GPT models and can lead to cascading irregularities in syntax
def summarise_conversation(conversation_history):
    summary_prompt = (
        "Summarize the following conversation in a few sentences. Focus on key details relevant to the murder mystery.\n\n"
        f"Conversation history:\n{conversation_history}"
    )
    response = openai.chat.completions.create(
        model="gpt-4o-mini", 
        messages=[
            {"role": "system", "content": "You are a detective's assistant summarizing a conversation in a murder mystery. Only summarise the speech, do not analyse underlying intentions or provide commentary and ensure not to assert anything as fact, use langauge such as person A claims, person B questions, person C denies, etc. If two or more characters provide conflicting / mutually-exclusive series of event then this should be highlighted."},
            {"role": "user", "content": summary_prompt}
        ],
        max_tokens=300,
    )
    return response.choices[0].message.content

# Generate single vote
def generate_voting_prompt(character, memory, rules, conversation_summary):
    """Generate a prompt for the AI to vote on who they think the killer is."""
    prompt = (
        f"Based on the following summary of the conversation so far, please decide who you think the killer is. "
        f"Consider any suspicious behavior or inconsistent alibis, if unsure simply state NONE. "
        f"You cannot vote for yourself.\n\n"
        f"Conversation summary (Hearsay):\n{conversation_summary}\n\n"
        f"Your memory (Matter-of-fact):\n{memory}\n\n"
        f"Who do you think is the killer? Respond only with a character name or NONE if unsure."
    )

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": f"You are {character}, a character in a murder mystery game. {rules}"},
            {"role": "user", "content": prompt}
        ],
        max_tokens=20
    )
    
    vote = response.choices[0].message.content.strip().replace(".","")
    #print(f"{character} votes for: {vote}")
    return vote

# Collect all votes
def ai_vote_for_killer(player_name, characters, conversation_summary, rules, round_number):
    """Conduct the voting phase where each alive character votes for who they think the killer is."""
    votes = {}
    votes[player_name] = input("Who are you voting for?")

    for character in characters:
        if characters[character]["alive"]:
            # Prompt the AI to vote for the killer
            vote = generate_voting_prompt(character, characters[character]["memory"], rules, conversation_summary)
            
            # Ensure they don't vote for themselves
            while vote == character or (vote != player_name and vote not in characters and vote != "NONE"):
                vote = random.choice([char for char in characters if char != character and characters[char]["alive"]])

            votes[character] = vote
            print(f"{character} voted for {vote}.")
        
    vote_counts = Counter(votes.values())
    most_common = vote_counts.most_common()

    print(conversation_summary)

    for character in characters:
        if characters[character]["alive"] == True:
            characters[character]["memory"].pop(-1)
            characters[character]["memory"].append(f"Final Conversation Summary for the day: \n {conversation_summary} \n\n")

    if most_common[0][1] == most_common[1][1]:
        print(f"There was a tie between {most_common[0][0]} and {most_common[1][0]}, no-one was voted out.")
    elif most_common[0][0] == "NONE":
        print(f"The group decided not to rush to conclusions and no-one was voted out.")

    else:
        voted_out = most_common[0][0]
        if voted_out == player_name:
            final_score = 5 - sum(1 for character in characters if characters[character]["alive"])
            print(f"You were voted out! Unlucky, you lose. Final score = {final_score} / 5")
            return characters, True
        else:
            print(f"{voted_out} was voted out! They were hanged for their crimes.")
            characters[voted_out]["alive"] = False
            for character in characters:
                if characters[character]["alive"] == True:
                    characters[character]["memory"].append(f"During the Voting Phase, you voted for {votes[character]} as you decided they were the most suspiscious. \n In the end, {voted_out} was voted out by the group and was hanged for their crimes, however, it is revealed that {voted_out} was NOT the killer. The game continues. \n  Day {round_number} (Conversation Phase {round_number}) END \n")
    return characters, False



room_facts = {
    "Attic": "There is a distinct humming noise.",
    "Kitchen": "The oven is broken.",
    "Library": "The smell of old books is overwhelming.",
    "Basement": "There are strange, echoing noises.",
    "Cellar": "There are 4 kegs of beer."
}


def find_characters_yet_to_speak(current_conversation, alive_characters):
    # Initialize a set to track characters who have spoken
    spoken_characters = set()
    
    # Loop through current_conversation to extract speakers
    for message in current_conversation:
        # Split the message on the first colon (":") to get the speaker
        speaker = message.split(":")[0].strip()  # Extract the speaker's name
        
        # Add the speaker to the set
        spoken_characters.add(speaker)
    
    # Find characters in alive_characters who haven't spoken
    characters_yet_to_speak = [char for char in alive_characters if char not in spoken_characters]
    
    return characters_yet_to_speak


def check_direct_address(most_recent_message, characters, player_name):
    """Check if a character is directly addressed by name in the player's message."""
    most_recent_message = most_recent_message.split(":")[1]

    #First we check if only a single person was mentioned in the message
    mentioned_characters = []
    for character in characters:
        if characters[character]["alive"] and character in most_recent_message:
            mentioned_characters.append(character)

    if player_name in most_recent_message:
        mentioned_characters.append(player_name)

    if len(mentioned_characters) == 1:
        return mentioned_characters[0]
    
    #Next we check if the player was directly addressed as the first word of the message
    if most_recent_message.strip().replace(",","").split(" ")[0] == player_name:
        return player_name
    
    #Then we check if any NPCs were directly addressed - unfortunately there is some indexing priority here but that shouldn't really be an issue.
    for character in characters:
        if characters[character]["alive"] and (f". {character}" in most_recent_message or (most_recent_message.strip().replace(",", "").replace("'"," ").split(" ")[0] == character or most_recent_message.strip().split(",")[0] == character)):
            return character  
    
    #Then we check if the player was directly addressed at a later part of the message
    if f". {player_name}" in most_recent_message:
        return player_name
    return None

# # Conversation Phase
def conversation_stage(player_name, rooms, characters, old_crime_scenes, round_number):
    print("\nConversation Phase begins. Detective asks the first question.")

    short_term_memory = 2
    yap_counter = 0
    minimum_to_call_vote = 1 #Remember to change back to 20!

    current_conversation = []
    most_recent_messageS = []

    alive_characters = [char for char in characters if characters[char]["alive"]]
    dead_characters = [char for char in characters if not characters[char]["alive"]]
    available_rooms = [r for r in rooms if r not in old_crime_scenes]


    alive_characters.append(player_name)
    NPCs = [char for char in characters if characters[char]["alive"]]


    first_to_answer = random.choice(alive_characters)

    most_recent_message = f"Detective: {first_to_answer}, let's start with you. Where were you last night? \n"
    print(most_recent_message)
    most_recent_messageS.append(most_recent_message)
    current_conversation.append(most_recent_message)

    if first_to_answer == player_name:
        print(f"Possible Locations: {', '.join(available_rooms)}")
        player_response = handle_player_interjection()
        most_recent_message = f"{player_name}: {player_response}"
        most_recent_messageS.append(most_recent_message)
        current_conversation.append(most_recent_message)
    
    
    for character in characters:
        characters[character]["memory"].append(f"\n BEGIN Day {round_number} (Conversation Phase {round_number}): \n Alive suspects = {alive_characters}, Dead suspects = {dead_characters}. \n")
        characters[character]["memory"].append(f"Rooms that were able to be occupied last night = {available_rooms}, anyone who claims to be anywhere other than one of these rooms is lying. \n")
    
    while True:

        #Ensure no-one gets away with saying nothing
        if len(current_conversation) >= 10 and len(current_conversation) % 5 == 0:
            unspoken = find_characters_yet_to_speak(current_conversation, alive_characters)
            if len(unspoken) >= 1:
                forced_to_speak_up = random.choice(unspoken)
                most_recent_message = f"Detective: Hold on a moment. {forced_to_speak_up}; you've remained awfully quiet - care to explain where you were last night?"
                print(most_recent_message, "\n")
                most_recent_messageS.append(most_recent_message)
                current_conversation.append(most_recent_message)

        #After a certain length, player can call the vote.
        if len(current_conversation) == minimum_to_call_vote: 
            print("CONVERSATION LENGTH REQUIREMENT MET: TYPE 'vote' AT ANY TIME TO INITIATE VOTING")

        mentioned = check_direct_address(most_recent_message, characters, player_name)
        #print("Mentioned: ", mentioned)
        if mentioned != None and mentioned != most_recent_message.split(":")[0]:
            if mentioned != player_name:
                if yap_counter >= 3 and player_interject():
                    player_response = handle_player_interjection()
                    if (player_response.strip().lower() == "vote" and len(current_conversation) >= minimum_to_call_vote):
                        conversation_summary = summarise_conversation(current_conversation)
                        characters, game_over = ai_vote_for_killer(player_name, characters, conversation_summary, rules, round_number)
                        return characters, game_over

                    most_recent_message = f"{player_name}: {player_response}"
                    yap_counter = 0
                else:
                    conversation_summary = summarise_conversation(current_conversation)
                    response = generate_conversation_speech(mentioned, characters, rules, conversation_summary, most_recent_message).replace("{", "").replace("}", "")
                    most_recent_message = f"{mentioned}: {response}"
                    yap_counter += 1
                    print(most_recent_message, "\n")

                most_recent_messageS.append(most_recent_message)
                if len(most_recent_messageS) > short_term_memory :
                    most_recent_messageS.pop(0)
                current_conversation.append(most_recent_message)
            else:
                player_response = handle_player_interjection()

                if (player_response.strip().lower() == "vote" and len(current_conversation) >= minimum_to_call_vote):
                    conversation_summary = summarise_conversation(current_conversation)
                    characters, game_over = ai_vote_for_killer(player_name, characters, conversation_summary, rules, round_number)
                    return characters, game_over
                most_recent_message = f"{player_name}: {player_response}"

                most_recent_messageS.append(most_recent_message)
                if len(most_recent_messageS) > short_term_memory :
                    most_recent_messageS.pop(0)
                current_conversation.append(most_recent_message)

                yap_counter = 0

        
        else:
            if most_recent_message.split(":")[0] != player_name and player_interject():

                player_response = handle_player_interjection()
                if (player_response.strip().lower() == "vote" and len(current_conversation) >= minimum_to_call_vote):
                    conversation_summary = summarise_conversation(current_conversation)
                    characters, game_over = ai_vote_for_killer(player_name, characters, conversation_summary, rules, round_number)
                    return characters, game_over
                most_recent_message = f"{player_name}: {player_response}"

                most_recent_messageS.append(most_recent_message)
                if len(most_recent_messageS) > short_term_memory :
                    most_recent_messageS.pop(0)
                current_conversation.append(most_recent_message)

                yap_counter = 0
                
            else:
                #print("No direct address detected and player passes")
                conversation_summary = summarise_conversation(current_conversation)
                #print(conversation_summary)
                responses = generate_responses(characters, rules,  conversation_summary, most_recent_messageS)
                best_response, speaker = select_best_response(responses, conversation_summary, most_recent_messageS, NPCs)
                most_recent_message = f"{speaker}: {best_response}"

                print(most_recent_message, "\n")
                most_recent_messageS.append(most_recent_message)
                if len(most_recent_messageS) > short_term_memory :
                    most_recent_messageS.pop(0)
                current_conversation.append(most_recent_message)


        
        if len(current_conversation) >= 40:
            conversation_summary = summarise_conversation(current_conversation)
            characters, game_over = ai_vote_for_killer(player_name, characters, conversation_summary, rules, round_number)
            return characters, game_over


def action_phase(player_name, rooms, characters, old_crime_scenes, round_number):
    print(f"Action phase {round_number} begins.\n")
    new_crime_scenes = []
    # Room assignments for each character
    room_assignments = {room: [] for room in rooms}
    
    # Randomly assign each NPC to a room
    for character in characters:
        if characters[character]["alive"]:
            room = random.choice([r for r in rooms if r not in old_crime_scenes])
            room_assignments[room].append(character)
    
    # Display room assignments for debugging purposes
    print("Room Assignments:", room_assignments)

    # Prompt the player to choose a room
    rooms_visited = []
    available_rooms = [r for r in rooms if r not in old_crime_scenes]
    print(f"Available Rooms: {available_rooms}")
    player_room = input(f"{player_name}, choose a room to stay the night: ")

    # Check if the player's chosen room is valid
    while player_room not in available_rooms:
        print(f"{player_room} is not a valid room. Please choose from {available_rooms}. \n")
        player_room = input(f"{player_name}, choose a room to stay the night: ")

    # Handle the outcome based on the occupants of the chosen room
    occupants = room_assignments[player_room]
    rooms_visited.append(player_room)
    new_crime_scenes.append(player_room)

    had_to_change_rooms = False


    while len(occupants) == 0:
        # No one is in the room, player gets another choice
        had_to_change_rooms = True
        print(f"The {player_room} is empty. You must move to another room but {player_room} will also become a crime scene. \n")

        
        
        available_rooms = [r for r in rooms if r not in new_crime_scenes and r not in old_crime_scenes]

        player_room = input(f"{player_name}, choose another room: ")
        
        while player_room not in available_rooms:
            print(f"{player_room} is not a valid room. Please choose from {available_rooms}. \n")
            player_room = input(f"{player_name}, choose a room to stay the night: ")
        
        rooms_visited.append(player_room)
        new_crime_scenes.append(player_room)
        occupants = room_assignments[player_room]

    #Re-Initialise Action-Phase Information, Including Key Take-Aways From Previous Nights
    for character in characters:
        if characters[character]["alive"] == True:
            characters[character]["memory"] = [f"BEGIN Action Phase Information: \n"]
            for key_takeaway in characters[character]["key-takeaways"]:
                    characters[character]["memory"].append(key_takeaway)

    if len(occupants) == 1:
        # One character in the room, kill them
        victim = occupants[0]
        characters[victim]["alive"] = False
        new_crime_scenes.append(player_room)
        print(f"You killed {victim} in the {player_room}. The {player_room} is now a crime scene.")
        
    
    elif len(occupants) == 2:
        # 2 characters, player must choose who to kill
        print(f"The {player_room} is occupied by {', '.join(occupants)}.")
        victim = input(f"{player_name}, choose a character to kill: {occupants}: ")
        
        while victim not in occupants:
            print(f"{victim} is not in the {player_room}.")
            victim = input(f"{player_name}, choose a character to kill: {occupants}: ")

        characters[victim]["alive"] = False
        new_crime_scenes.append(player_room)
        print(f"You killed {victim} in the {player_room}. The {player_room} is now a crime scene.")
        
        # Update memory for the witness
        for occupant in occupants:
            if occupant != victim:
                characters[occupant]["key-takeaways"].append(f"{player_name} is the killer! You witnessed {player_name} kill {victim} in the {player_room} during a previous night! This is an un-deniable fact. \n")

                if had_to_change_rooms:
                    characters[occupant]["memory"].append(f"ACTION PHASE INFORMATION FROM LAST NIGHT: \n You ({occupant}) spent the night in the {player_room} with {player_name} and {victim} and no-one else. \n {player_name} arrives late from another room and you witness {player_name} killing {victim} in the {player_room} - {player_name} is the killer. \n Expect {player_name} to lie, cheat and do everything possible to shift blame, you must defend your innocence. \n Since you know for a fact that {player_name} is the killer, DO NOT EVER ATTEMPT TO CAST SUSPISCION ON ANYONE OTHER THAN {player_name}. \n")
                else:
                    characters[occupant]["memory"].append(f"ACTION PHASE INFORMATION FROM LAST NIGHT: \n You ({occupant}) spent the night in the {player_room} with {player_name} and {victim} and no-one else. \n You witness {player_name} killing {victim} in the {player_room} - {player_name} is the killer. \n Expect {player_name} to lie, cheat and do everything possible to shift blame, you must defend your innocence. \n Since you know for a fact that {player_name} is the killer,  DO NOT EVER ATTEMPT TO CAST SUSPISCION ON ANYONE OTHER THAN {player_name}. \n")
        
    
    else:
        # Multiple characters, player must choose who to kill
        print(f"The {player_room} is occupied by {', '.join(occupants)}.")
        victim = input(f"{player_name}, choose a character to kill: {occupants}: ")
        
        while victim not in occupants:
            print(f"{victim} is not in the {player_room}.")
            victim = input(f"{player_name}, choose a character to kill: {occupants}: ")

        characters[victim]["alive"] = False
        new_crime_scenes.append(player_room)
        print(f"You killed {victim} in the {player_room}. The {player_room} is now a crime scene.")
        
        # Update memory for each witness
        for occupant in occupants:
            if occupant != victim:
                other_occupants = [o for o in occupants if (o != occupant) and (o != victim)]
                characters[occupant]["key-takeaways"].append(f"{player_name} is the killer! You witnessed {player_name} kill {victim} during a previous night! This is an un-deniable fact; {', and'.join(other_occupants)} were there with you to witness and can back you up! \n")

                if had_to_change_rooms:
                    characters[occupant]["memory"].append(f"ACTION PHASE INFORMATION FROM LAST NIGHT: \n You ({occupant}) spent the night in the {player_room} with {player_name}, {', '.join(other_occupants)} and {victim} and no-one else. \n {player_name} arrives late from another room and you witness {player_name} killing {victim} in the {player_room} - {player_name} is the killer. \n Expect {player_name} to lie, cheat and do everything possible to shift blame, you must defend your innocence. \n Since you know for a fact that {player_name} is the killer, DO NOT EVER ATTEMPT TO CAST SUSPISCION ON ANYONE OTHER THAN {player_name}. \n")
                else:
                    characters[occupant]["memory"].append(f"ACTION PHASE INFORMATION FROM LAST NIGHT: \n You ({occupant}) spent the night in the {player_room} with {player_name}, {', '.join(other_occupants)} and {victim} and no-one else. \n You witness {player_name} killing {victim} in the {player_room} - {player_name} is the killer. \n Expect {player_name} to lie, cheat and do everything possible to shift blame, you must defend your innocence. \n Since you know for a fact that {player_name} is the killer, DO NOT EVER ATTEMPT TO CAST SUSPISCION ON ANYONE OTHER THAN {player_name}. \n")


                characters[occupant]["memory"].append(f"{' and '.join(other_occupants)} also spent the night with you in the {player_room} and also witnessed the murder, they are NOT the killer and can validate your testimony. \n")
        
    
    # Update memory for other characters not in the room
    for room, occupants in room_assignments.items():
        if room != player_room:
            for occupant in occupants:
                if characters[occupant]["alive"]:

                    if len(occupants) >=2:
                        other_occupants = [o for o in occupants if (o != occupant)]
                        
                        characters[occupant]["key-takeaways"].append(f"You know with certainty that {' and '.join(other_occupants)} is / are NOT the killer! You spent a previous night with them while the killer was active and they never left your sight. You MUST defend {' and '.join(other_occupants)} from suspiscion since you are certain of their innocence!")

                        characters[occupant]["memory"].append(
                            f"ACTION PHASE INFORMATION FROM LAST NIGHT: \n You ({occupant}) spent the night in the {room}, {' and '.join(other_occupants)} was there with you and no-one else. \n"
                        )
                        characters[occupant]["memory"].append(
                            f"Because of this you know for a FACT that {' and '.join(other_occupants)} are NOT the killer because you can support their alibi, you MUST assert the innocence of {' and '.join(other_occupants)} and defend them from suspiscion. \n"
                        )
                    else:
                        characters[occupant]["memory"].append(
                            f"ACTION PHASE INFORMATION FROM LAST NIGHT: \n You ({occupant}) spent the night in the {room}, no-one else was there with you. \n \n"
                        )

                    characters[occupant]["memory"].append(f"In the morning the Detective reveals to everyone that {victim}'s corpse was found in the {player_room}. \n")

                    if had_to_change_rooms:
                        characters[occupant]["memory"].append(f"The killer must be someone who spent the night in the {player_room}, however the Detective also tells everyone that the killer silently made their way from the {' to the '.join(rooms_visited)} during the night and so all have been marked as crime scenes. \n")
                    else:
                        characters[occupant]["memory"].append(f"The killer must be someone who spent the night in the {player_room}. \n")

    for character in characters:
        if characters[character]["alive"]:
            characters[character]["memory"].append(f" \n END Action Phase Information \n")
            #print("\n".join(characters[character]["memory"]))
            #print(character, characters[character]["key-takeaways"])
    
    return characters, new_crime_scenes

# Handle player interjection
def handle_player_interjection():
    player_input = input("What would you like to say?")
    #print(f"You: {player_input} \n")
    return player_input

# Check if the player wants to interject
def player_interject():
    choice = input("Do you want to interject? (y/n): ").strip().lower()
    return choice == "y"

# Main game loop
def start_game():
    player_name = input("You there, what is your name?")
    print("The game begins with an action stage.")
    round_number = 0
    # Character profiles
    characters = {
        "Jerry": {"alive": True, "profile": ["You are Jerry, you are soft-spoken and introverted."], "memory": [], "key-takeaways": [f"From your experiences in previous nights (before last night) you know, as FACT, that: \n"]},
        "Dave": {"alive": True, "profile": ["You are Dave, you are a friendly guy but a bit of a follower. You prefer to blend into the background."], "memory": [], "key-takeaways": [f"From your experiences in previous nights (before last night) you know, as FACT, that: \n"]},
        "Owen": {"alive": True, "profile": ["You are Owen, you have a short temper and a bit of an ego."], "memory": [], "key-takeaways": [f"From your experiences in previous nights (before last night) you know, as FACT, that: \n"]},
        "Hank": {"alive": True, "profile": ["You are Hank, you're always suspicious of everyone around you, including the Detective."], "memory": [], "key-takeaways": [f"From your experiences in previous nights (before last night) you know, as FACT, that: \n"]},
        "Debra": {"alive": True, "profile": ["You are Debra, you're somewhere between apathetic and non-chalant, nothing really phases you."], "memory": [], "key-takeaways": [f"From your experiences in previous nights (before last night) you know, as FACT, that: \n"]}
    }
    # Rooms in the mansion
    rooms = ["Attic", "Kitchen", "Library", "Basement", "Cellar"]
    old_crime_scenes = []

    game_over = False

    while not game_over:
        round_number += 1
        # Action Stage
        characters, new_crime_scenes = action_phase(player_name, rooms, characters, old_crime_scenes, round_number)
        
        if sum(1 for character in characters if characters[character]["alive"]) == 0:
            print("Congratulations! You were able to murder everyone without being caught! Score: 5 / 5")
            break
        # Conversation Stage
        characters, game_over = conversation_stage(player_name, rooms, characters, old_crime_scenes, round_number)

        for scene in new_crime_scenes:
            old_crime_scenes.append(scene)





if __name__ == "__main__":
    start_game()