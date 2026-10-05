package {{package}};

import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.ModContainer;
import net.neoforged.fml.common.Mod;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Mod({{class}}.MOD_ID)
public final class {{class}} {

    public static final String MOD_ID = "{{id}}";
    public static final Logger LOGGER = LoggerFactory.getLogger("{{name}}");

    public {{class}}(IEventBus modBus, ModContainer container) {
        LOGGER.info("{{name}} loaded");
    }
}
