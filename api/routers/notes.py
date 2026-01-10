from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from loguru import logger

from api.models import NoteCreate, NoteResponse, NoteUpdate
from open_notebook.domain.notebook import Note
from open_notebook.exceptions import InvalidInputError
from api.deps import get_current_user_id

router = APIRouter()


@router.get("/notes", response_model=List[NoteResponse])
async def get_notes(
    notebook_id: Optional[str] = Query(None, description="Filter by notebook ID"),
    user_id: str = Depends(get_current_user_id),
):
    """Get all notes with optional notebook filtering."""
    try:
        if notebook_id:
            # Get notes for a specific notebook
            from open_notebook.domain.notebook import Notebook
            notebook = await Notebook.get(notebook_id)
            if not notebook or str(notebook.owner) != str(user_id):
                raise HTTPException(status_code=404, detail="Notebook not found")
            notes = [n for n in await notebook.get_notes() if str(n.owner) == str(user_id)]
        else:
            # Get all notes
            notes = [n for n in await Note.get_all(order_by="updated desc") if str(n.owner) == str(user_id)]
        
        return [
            NoteResponse(
                id=note.id or "",
                title=note.title,
                content=note.content,
                note_type=note.note_type,
                created=str(note.created),
                updated=str(note.updated),
            )
            for note in notes
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching notes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching notes: {str(e)}")


@router.post("/notes", response_model=NoteResponse)
async def create_note(note_data: NoteCreate, user_id: str = Depends(get_current_user_id)):
    """Create a new note."""
    try:
        # Auto-generate title if not provided and it's an AI note
        title = note_data.title
        if not title and note_data.note_type == "ai" and note_data.content:
            from open_notebook.graphs.prompt import graph as prompt_graph
            prompt = "Based on the Note below, please provide a Title for this content, with max 15 words"
            result = await prompt_graph.ainvoke(
                {  # type: ignore[arg-type]
                    "input_text": note_data.content,
                    "prompt": prompt
                }
            )
            title = result.get("output", "Untitled Note")
        
        # Validate note_type
        note_type: Optional[Literal["human", "ai"]] = None
        if note_data.note_type in ("human", "ai"):
            note_type = note_data.note_type  # type: ignore[assignment]
        elif note_data.note_type is not None:
            raise HTTPException(status_code=400, detail="note_type must be 'human' or 'ai'")

        new_note = Note(
            title=title,
            content=note_data.content,
            note_type=note_type,
            owner=user_id,
        )
        await new_note.save()
        
        # Add to notebook if specified
        if note_data.notebook_id:
            from open_notebook.domain.notebook import Notebook
            notebook = await Notebook.get(note_data.notebook_id)
            if not notebook or str(notebook.owner) != str(user_id):
                raise HTTPException(status_code=404, detail="Notebook not found")
            await new_note.add_to_notebook(note_data.notebook_id)
        
        return NoteResponse(
            id=new_note.id or "",
            title=new_note.title,
            content=new_note.content,
            note_type=new_note.note_type,
            created=str(new_note.created),
            updated=str(new_note.updated),
        )
    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating note: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating note: {str(e)}")


@router.get("/notes/{note_id}", response_model=NoteResponse)
async def get_note(note_id: str, user_id: str = Depends(get_current_user_id)):
    """Get a specific note by ID."""
    try:
        note = await Note.get(note_id)
        if not note or str(note.owner) != str(user_id):
            raise HTTPException(status_code=404, detail="Note not found")
        
        return NoteResponse(
            id=note.id or "",
            title=note.title,
            content=note.content,
            note_type=note.note_type,
            created=str(note.created),
            updated=str(note.updated),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching note {note_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching note: {str(e)}")


@router.put("/notes/{note_id}", response_model=NoteResponse)
async def update_note(note_id: str, note_update: NoteUpdate, user_id: str = Depends(get_current_user_id)):
    """Update a note."""
    try:
        note = await Note.get(note_id)
        if not note or str(note.owner) != str(user_id):
            raise HTTPException(status_code=404, detail="Note not found")
        
        # Update only provided fields
        if note_update.title is not None:
            note.title = note_update.title
        if note_update.content is not None:
            note.content = note_update.content
        if note_update.note_type is not None:
            if note_update.note_type in ("human", "ai"):
                note.note_type = note_update.note_type  # type: ignore[assignment]
            else:
                raise HTTPException(status_code=400, detail="note_type must be 'human' or 'ai'")

        await note.save()

        return NoteResponse(
            id=note.id or "",
            title=note.title,
            content=note.content,
            note_type=note.note_type,
            created=str(note.created),
            updated=str(note.updated),
        )
    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating note {note_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating note: {str(e)}")


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str, user_id: str = Depends(get_current_user_id)):
    """Delete a note."""
    try:
        note = await Note.get(note_id)
        if not note or str(note.owner) != str(user_id):
            raise HTTPException(status_code=404, detail="Note not found")
        
        await note.delete()
        
        return {"message": "Note deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting note {note_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting note: {str(e)}")
