import asyncio
from sqlalchemy import select
from db import init_db, sync_draft_to_production, AsyncSessionLocal, DraftMenuButton, ProductionMenuButton, User

async def run_test():
    print("Starting Database Verification Tests...")
    
    # 1. Initialize DB and tables
    print("Initializing database and tables...")
    await init_db()
    print("Database tables created successfully.")
    
    async with AsyncSessionLocal() as session:
        # Clear existing buttons from previous tests
        from sqlalchemy import delete
        await session.execute(delete(DraftMenuButton))
        await session.execute(delete(ProductionMenuButton))
        await session.commit()
        
        # 2. Add some test buttons to draft menu
        print("Adding mock staging buttons...")
        
        # Add a folder (submenu)
        folder = DraftMenuButton(
            title="Fifth Grade Preparatory",
            button_type="menu",
            order_index=0,
            button_style="success"
        )
        session.add(folder)
        await session.flush()  # gets the ID of folder
        
        # Add a sub-folder
        subfolder = DraftMenuButton(
            parent_id=folder.id,
            title="IQ Section",
            button_type="menu",
            order_index=0,
            button_style="primary"
        )
        session.add(subfolder)
        await session.flush()
        
        # Add a link inside the sub-folder
        link = DraftMenuButton(
            parent_id=subfolder.id,
            title="Lec 1: Intro PDF",
            button_type="link",
            order_index=0,
            button_style="danger",
            source_chat_id=-100123456789,
            source_message_id=987,
            credit_text="@doctor_iq / https://t.me/some_channel/987"
        )
        session.add(link)
        await session.commit()
        print("Mock staging menu structure saved in draft_menu_buttons.")
        
        # Verify drafts exist
        draft_count_res = await session.execute(select(DraftMenuButton))
        draft_count = len(draft_count_res.scalars().all())
        print(f"Total draft buttons created: {draft_count}")
        assert draft_count == 3, f"Expected 3 draft buttons, got {draft_count}"
        
        # Verify production is currently empty
        prod_count_res = await session.execute(select(ProductionMenuButton))
        prod_count = len(prod_count_res.scalars().all())
        print(f"Total live buttons before publishing: {prod_count}")
        assert prod_count == 0, f"Expected 0 live buttons, got {prod_count}"
        
    # 3. Test Sync
    print("Synchronizing staging menu to live menu...")
    await sync_draft_to_production()
    print("Synchronization completed.")
    
    async with AsyncSessionLocal() as session:
        # Verify production now contains the same items
        prod_count_res = await session.execute(select(ProductionMenuButton))
        prod_buttons = prod_count_res.scalars().all()
        print(f"Total live buttons after publishing: {len(prod_buttons)}")
        assert len(prod_buttons) == 3, f"Expected 3 live buttons, got {len(prod_buttons)}"
        
        # Verify child relations and properties are preserved
        stmt = select(ProductionMenuButton).where(ProductionMenuButton.parent_id == None)
        root_res = await session.execute(stmt)
        root_btn = root_res.scalars().first()
        print(f"Root button title: {root_btn.title}")
        assert root_btn.title == "Fifth Grade Preparatory", "Root title mismatch!"
        assert root_btn.button_style == "success", "Root style mismatch!"
        
        stmt_sub = select(ProductionMenuButton).where(ProductionMenuButton.parent_id == root_btn.id)
        sub_btn = (await session.execute(stmt_sub)).scalars().first()
        print(f"  Sub-folder title: {sub_btn.title}")
        assert sub_btn.title == "IQ Section", "Sub-folder title mismatch!"
        assert sub_btn.button_style == "primary", "Sub-folder style mismatch!"
        
        stmt_link = select(ProductionMenuButton).where(ProductionMenuButton.parent_id == sub_btn.id)
        link_btn = (await session.execute(stmt_link)).scalars().first()
        print(f"    Link button: {link_btn.title}")
        assert link_btn.title == "Lec 1: Intro PDF", "Link button title mismatch!"
        assert link_btn.button_style == "danger", "Link button style mismatch!"
        assert link_btn.credit_text == "@doctor_iq / https://t.me/some_channel/987", "Credits mismatch!"
        
    print("\nALL DATABASE TESTS PASSED SUCCESSFULLY! Schema and tree-sync are 100% verified.")

if __name__ == "__main__":
    asyncio.run(run_test())
